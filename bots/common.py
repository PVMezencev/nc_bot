import asyncio
import hashlib
import hmac
import json
from datetime import datetime
from typing import Dict, Any

import httpx

import secrets

from pydantic import BaseModel


class StateData(BaseModel):
    data: Dict[str, Any]
    state: str


class StateContext:
    def __init__(self):
        self.data: Dict[str, StateData] = dict()
        self.__lock = asyncio.Lock()

    async def get_state(self, chat: str) -> StateData:
        async with self.__lock:
            return self.data.get(chat)

    async def set_state(self, chat: str, state: str):
        async with self.__lock:
            __current_state_data = await self.get_state(chat)
            if not __current_state_data:
                __current_state_data = StateData(state=state, data=dict())
            else:
                __current_state_data.state = state
            self.data[chat] = __current_state_data

    async def get_data(self, chat: str) -> Dict[str, Any]:
        async with self.__lock:
            __current_state_data = await self.get_state(chat)
            if __current_state_data:
                return __current_state_data.data

    async def set_data(self, chat: str, data: Dict[str, Any]):
        async with self.__lock:
            __current_state_data = await self.get_state(chat)
            if not __current_state_data:
                __current_state_data = StateData(state="", data=data)
            else:
                __current_data = __current_state_data.data
                __current_data.update(data)
                __current_state_data.data = __current_data

            self.data[chat] = __current_state_data

    async def clear(self, chat: str):
        async with self.__lock:
            try:
                del self.data[chat]
            except KeyError:
                return


ChatState = StateContext()


class Bot:
    def __init__(self, bot_name, bot_token, nc_url):
        self.bot_name = bot_name
        self.bot_token = bot_token
        self.nc_url = nc_url

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
        """Логирование входящих запросов"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "user": data.get("user", {}).get("id"),
            "room": data.get("room", {}).get("token"),
            "message_preview": str(data.get("message", {}).get("message", ""))[:50]
        }
        print(f"Webhook received: {json.dumps(log_entry, ensure_ascii=False)}")

    async def handle_command(self, message: str, user_id: str = None, room_token: str = None) -> str:
        print(f'implement me for bot {self.__class__}')
        return ""

    async def process_message(self, data: Dict[str, Any]) -> Dict[str, Any] | None:
        """Обработка входящего сообщения"""
        # Извлекаем данные
        try:
            message_json = data.get("object", {}).get("content", "").strip()
            message_obj = json.loads(message_json)
            message_text = message_obj.get("message", "").strip()
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
