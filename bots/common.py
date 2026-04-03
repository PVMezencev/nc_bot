import asyncio
import hashlib
import hmac
import json
from datetime import datetime
from typing import Dict, Any, Optional

import httpx

import secrets

from pydantic import BaseModel


class StateData(BaseModel):
    data: Dict[str, Any]
    state: str


class StateContext:
    def __init__(self):
        self.__data: Dict[str, StateData] = dict()
        self.__lock = asyncio.Lock()

    async def get_state(self, chat: str) -> str:
        async with self.__lock:
            return self.__data.get(chat, StateData(state="", data={})).state

    async def set_state(self, chat: str, state: str):
        print(f'start set state {chat} {state}')
        async with self.__lock:
            if chat in self.__data:
                self.__data[chat].state = state
            else:
                self.__data[chat] = StateData(state=state, data={})
        print(f'stop set state {chat} {state}')

    async def get_data(self, chat: str) -> Optional[Dict[str, Any]]:
        print(f'start get_data {chat}')
        async with self.__lock:
            state_data = self.__data.get(chat)
            if state_data:
                print(f'state_data {state_data}')
                return state_data.data.copy()  # Возвращаем копию
            print(f'state_data None')
            return None

    async def set_data(self, chat: str, data: Dict[str, Any]):
        async with self.__lock:
            if chat in self.__data:
                self.__data[chat].data.update(data)
            else:
                self.__data[chat] = StateData(state="", data=data.copy())

    async def clear(self, chat: str):
        async with self.__lock:
            self.__data.pop(chat, None)


ChatState = StateContext()


class Bot:
    def __init__(self, bot_name, nc_url):
        self.bot_name = bot_name
        self.nc_url = nc_url
        self.HANDLER_FIELD = 'handler'
        self.HELP_TEXT_FIELD = 'help'
        self.ACCESS_FIELD = 'access'
        self.command_handlers = {}

        import botsecrets
        self.bot_token = botsecrets.BOT_SECRETS.get(self.bot_name)

    # Валидация подписи вебхука
    def verify_signature(self,
                         payload: bytes,
                         signature: str,
                         random: str,  # Важно: добавляем параметр random
                         ) -> bool:
        """
        Проверка подписи вебхука Nextcloud Talk
        Формат: hash_hmac('sha256', random . body, secret)
        """
        if not signature or not self.bot_token or not random:
            return False

        # Создаем дайджест: random + body
        digest = hmac.new(
            self.bot_token.encode('utf-8'),
            (random + payload.decode('utf-8')).encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        # Сравниваем (без учета регистра)
        return hmac.compare_digest(digest.lower(), signature.lower())

    # Функция для отправки сообщений в Nextcloud
    async def send_to_nextcloud(self, room_token: str, message: str, rely_to: int = None, silent=False):
        """Отправка сообщения в Nextcloud Talk"""
        url = f"{self.nc_url}/ocs/v2.php/apps/spreed/api/v1/bot/{room_token}/message"

        new_message_id = secrets.token_hex(64)
        # Generate a random header and signature
        RANDOM_HEADER = new_message_id
        MESSAGE_TO_SIGN = f"{RANDOM_HEADER}{message}"

        SECRET = self.bot_token  # Укажите ваш секретный ключ

        SIGNATURE = hmac.new(
            SECRET.encode(),
            MESSAGE_TO_SIGN.encode(),
            hashlib.sha256
        ).hexdigest()

        headers = {
            "OCS-APIRequest": "true",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Nextcloud-Talk-Bot-Random": RANDOM_HEADER,
            "X-Nextcloud-Talk-Bot-Signature": SIGNATURE,
        }

        message_payload = {"message": message, "referenceId": new_message_id, "silent": silent}
        if rely_to:
            message_payload["replyTo"] = rely_to
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(
                    url,
                    json=message_payload,
                    headers=headers,
                    timeout=30.0
                )
                response.raise_for_status()
                return new_message_id
            except Exception as e:
                print(f"Error sending to Nextcloud: {e}")

    # Функция логирования
    async def log_request(self, data: Dict[str, Any]):
        print(f"Webhook received: {json.dumps(data, ensure_ascii=False)}")

    async def handle_state(self, user_id, room_token, command) -> str | None:
        return

    async def handle_command(self, message: str, user_id: str = None, room_token: str = None) -> str:
        """Обработка команд"""

        # Извлекаем команду и аргументы
        parts = message.split()
        if not parts:
            return ""

        command = parts[0].lower().strip()
        args = parts[1:] if len(parts) > 1 else []

        # Выполняем команду
        handler = None
        cmd = self.command_handlers.get(command)
        if cmd:
            handler = self.command_handlers.get(command).get(self.HANDLER_FIELD)

        if handler:
            access = self.command_handlers.get(command).get(self.ACCESS_FIELD)
            if access and len(access) > 0:
                if user_id not in access:
                    return await self.forbidden(user_id)
            return await handler(args, user_id, room_token)
        else:
            state_result = await self.handle_state(user_id, room_token, command)
            if state_result:
                return state_result

            # Проверяем комбинированные команды типа "бот статус"
            if command == "бот" and args:
                sub_command = args[0].lower()
                if sub_command == "статус":
                    return await self.handle_bot_status(args[1:])

            return await self.handle_unknown(command)

    async def process_message(self, data: Dict[str, Any]) -> Dict[str, Any] | None:
        """Обработка входящего сообщения"""
        # Извлекаем данные
        try:
            message_json = data.get("object", {}).get("content", "").strip()
            message_obj = json.loads(message_json)
            message_text = message_obj.get("message", "")
            user_id_data = data.get("actor", {}).get("id", "")
            tails = user_id_data.split("/")
            if len(tails) > 1:
                user_id = tails[1]
            else:
                user_id = user_id_data
            room_token = data.get("target", {}).get("id", "")
            message_id = data.get("object", {}).get("id", "")

        except Exception as e:
            print(f'{datetime.now().isoformat("T")}: {e}')
            return

        message_text = message_text.strip()
        # Игнорируем сообщения без текста или от ботов
        if not message_text or user_id.startswith("bot-"):
            return

        # Проверяем, обращено ли сообщение к боту
        bot_mentioned = (
                f"@{self.bot_name}" in message_text or
                self.bot_name in message_text or
                message_text.startswith("!")
        )

        if not bot_mentioned:
            return

        # Очищаем сообщение от упоминания бота
        clean_message = message_text.replace(f"@{self.bot_name}", "").replace(self.bot_name,
                                                                              "").strip().removeprefix(
            "!")

        # Обработка команд
        response_text = await self.handle_command(clean_message, user_id, room_token)

        # Формируем ответ
        if response_text:
            return {
                "message": response_text,
                "replyTo": message_id,
                "room_token": room_token,
                "silent": False
            }

    async def handle_unknown(self, command: str, user_id=None) -> str:
        """Неизвестная команда"""
        return f"❌ Неизвестная команда: `{command}`\nИспользуйте `!помощь` для списка команд."

    async def forbidden(self, user_id=None) -> str:
        """Неизвестная команда"""
        return f"❌ Доступ запрещён!"

    async def handle_greet(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Приветствие"""
        return "Привет! 👋 Я бот. Напишите `!помощь` для списка команд."

    async def handle_time(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Текущее время"""
        now = datetime.now()
        return f"🕐 Текущее время: {now.strftime('%H:%M:%S %d.%m.%Y')} UTC"

    async def handle_bot_status(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Статус бота"""
        return "✅ Бот работает нормально\nВерсия: 1.0.0\nВремя работы: 24/7"
