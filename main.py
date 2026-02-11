import hashlib
import hmac
import json
import secrets
from datetime import datetime
from typing import Optional, Dict, Any

import httpx
from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.security import HTTPBearer
from pydantic import BaseModel


# Модели данных
class WebhookData(BaseModel):
    message: Dict[str, Any]
    user: Dict[str, Any]
    room: Dict[str, Any]
    conversation: Dict[str, Any]
    timestamp: int


class MessageResponse(BaseModel):
    response: str
    replyTo: Optional[str] = None
    silent: Optional[bool] = False


# Конфигурация
import config

app = FastAPI(
    title="Nextcloud Talk Bot",
    description="Бот для Nextcloud Talk",
    version="1.0.0"
)

security = HTTPBearer()


# Валидация подписи вебхука
def verify_signature(
        payload: bytes,
        signature: str,
        random: str,  # Важно: добавляем параметр random
        secret: str
) -> bool:
    """
    Проверка подписи вебхука Nextcloud Talk
    Формат: hash_hmac('sha256', random . body, secret)
    """
    if not signature or not secret or not random:
        return False

    # Создаем дайджест: random + body
    digest = hmac.new(
        secret.encode('utf-8'),
        (random + payload.decode('utf-8')).encode('utf-8'),
        hashlib.sha256
    ).hexdigest()

    # Сравниваем (без учета регистра)
    return hmac.compare_digest(digest.lower(), signature.lower())


# Обработка команд
class CommandHandler:
    @staticmethod
    async def handle_help(command_args: list = None, user_id=None) -> str:
        """Обработка команды помощи"""
        help_text = """
        🤖 *Доступные команды:*

        *Основные:*
        • `помощь` - показать это сообщение
        • `привет` - поздороваться с ботом
        • `время` - текущее время
        • `я` - мой профиль

        *Полезные:*
        • `погода [город]` - прогноз погоды
        • `курс [валюта]` - курс валюты
        • `перевод [текст]` - перевод текста

        *Администрирование:*
        • `бот статус` - статус бота
        • `бот пользователи` - список пользователей
        • `бот команды` - все команды

        *Формат:* `!ИмяБота команда параметры`
        """
        return help_text

    @staticmethod
    async def handle_greet(command_args: list = None, user_id=None) -> str:
        """Приветствие"""
        return "Привет! 👋 Я бот ЗАО СММ. Напишите `!помощь` для списка команд."

    @staticmethod
    async def handle_time(command_args: list = None, user_id=None) -> str:
        """Текущее время"""
        now = datetime.now()
        return f"🕐 Текущее время: {now.strftime('%H:%M:%S %d.%m.%Y')} UTC"

    @staticmethod
    async def handle_weather(command_args: list = None, user_id=None) -> str:
        """Прогноз погоды"""
        if not command_args:
            return "Укажите город. Пример: `погода Москва`"

        city = ' '.join(command_args)
        # Здесь можно добавить вызов API погоды
        return f"🌤️ Погода для {city}: +18°C, солнечно (шутка)"

    @staticmethod
    async def handle_bot_status(command_args: list = None, user_id=None) -> str:
        """Статус бота"""
        return "✅ Бот работает нормально\nВерсия: 1.0.0\nВремя работы: 24/7"

    @staticmethod
    async def handle_bot_user_profile(command_args: list = None, user_id=None) -> str:
        res = await get_user_profile(user_id)
        profile = res.get('data')
        manager = profile.get('manager')
        email = profile.get('email')
        displayname = profile.get('displayname')
        organisation = profile.get('organisation')
        role = profile.get('role')
        return ("👤 Мой профиль\n"
                f"Имя: {displayname}"
                f"Подразделение: {organisation}"
                f"Руководитель: {manager}"
                f"Должность: {role}"
                f"Почта: {email}"
                f"")

    @staticmethod
    async def handle_unknown(command: str, user_id=None) -> str:
        """Неизвестная команда"""
        return f"❌ Неизвестная команда: `{command}`\nИспользуйте `!помощь` для списка команд."


# Основной обработчик вебхуков
@app.post("/bots/{bot_name}")
async def handle_webhook(
        bot_name: str,
        request: Request,
        x_nextcloud_talk_signature: Optional[str] = Header(None, alias="X-Nextcloud-Talk-Signature"),
        x_nextcloud_talk_random: Optional[str] = Header(None, alias="X-Nextcloud-Talk-Random"),
):
    """Основной endpoint для вебхуков Nextcloud Talk"""

    # Получаем тело запроса
    try:
        payload = await request.body()
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")
    # Валидация подписи
    if config.WEBHOOK_SECRET:
        if not verify_signature(
            payload,
            x_nextcloud_talk_signature,
            x_nextcloud_talk_random,
            config.WEBHOOK_SECRET
        ):
            print(f"❌ Неверная подпись!")
            print(f"   Random: {x_nextcloud_talk_random}")
            print(f"   Signature received: {x_nextcloud_talk_signature}")
            print(f"   Signature calculated: [скрыто]")
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Логирование входящего запроса
    await log_request(data)

    # Обработка сообщения
    response = await process_message(data)

    print(f"response: {response}")

    if response:
        await send_to_nextcloud(response.get('room_token'), response.get('message'))

    return response


async def process_message(data: Dict[str, Any]) -> Dict[str, Any] | None:
    """Обработка входящего сообщения"""
    print(f'data: {data}')
    # Извлекаем данные
    try:
        message_json = data.get("object", {}).get("content", "").strip()
        message_obj = json.loads(message_json)
        message_text = message_obj.get("message", "").strip()
        user_id_data = data.get("user", {}).get("id", "")
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
            f"@{config.BOT_NAME}" in message_text or
            config.BOT_NAME in message_text or
            message_text.startswith("!")
    )

    if not bot_mentioned:
        return

    # Очищаем сообщение от упоминания бота
    clean_message = message_text.replace(f"@{config.BOT_NAME}", "").replace(config.BOT_NAME, "").strip().removeprefix("!")

    # Обработка команд
    response_text = await handle_command(clean_message, user_id, room_token)

    # Формируем ответ
    if response_text:
        return {
            "message": response_text,
            "replyTo": message_id,
            "room_token": room_token,
            "silent": False
        }


async def handle_command(message: str, user_id: str=None, room_token: str=None) -> str:
    """Обработка команд"""

    # Извлекаем команду и аргументы
    parts = message.split()
    if not parts:
        return ""

    command = parts[0].lower()
    args = parts[1:] if len(parts) > 1 else []

    # Маппинг команд
    command_handlers = {
        "помощь": CommandHandler.handle_help,
        "help": CommandHandler.handle_help,
        "привет": CommandHandler.handle_greet,
        "hello": CommandHandler.handle_greet,
        "время": CommandHandler.handle_time,
        "time": CommandHandler.handle_time,
        "погода": CommandHandler.handle_weather,
        "weather": CommandHandler.handle_weather,
        "бот": CommandHandler.handle_bot_status,
        "bot": CommandHandler.handle_bot_status,
        "status": CommandHandler.handle_bot_status,
        "me": CommandHandler.handle_bot_user_profile,
        "я": CommandHandler.handle_bot_user_profile,
    }

    # Выполняем команду
    handler = command_handlers.get(command)
    if handler:
        return await handler(args, user_id)
    else:
        # Проверяем комбинированные команды типа "бот статус"
        if command == "бот" and args:
            sub_command = args[0].lower()
            if sub_command == "статус":
                return await CommandHandler.handle_bot_status(args[1:])

        return await CommandHandler.handle_unknown(command)


async def get_user_profile(user_id):
    headers = {
        "OCS-APIRequest": "true",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                f'{config.NEXTCLOUD_URL}/ocs/v1.php/cloud/users/{user_id}',
                headers=headers,
                params={'format': 'json'},
                auth=(config.NEXTCLOUD_API_USER, config.NEXTCLOUD_API_PASSWORD),
                timeout=30.0
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Error sending to Nextcloud: {e}")


# Функция для отправки сообщений в Nextcloud
async def send_to_nextcloud(room_token: str, message: str, rely_to: int = None, silent=False):
    """Отправка сообщения в Nextcloud Talk"""
    url = f"{config.NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/bot/{room_token}/message"

    new_message_id = secrets.token_hex(64)
    # Generate a random header and signature
    RANDOM_HEADER = new_message_id
    MESSAGE_TO_SIGN = f"{RANDOM_HEADER}{message}"

    SECRET = config.WEBHOOK_SECRET  # Укажите ваш секретный ключ

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

    message_payload = {"message": message, "referenceId": new_message_id, "silent":silent}
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
async def log_request(data: Dict[str, Any]):
    """Логирование входящих запросов"""
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "user": data.get("user", {}).get("id"),
        "room": data.get("room", {}).get("token"),
        "message_preview": str(data.get("message", {}).get("message", ""))[:50]
    }
    print(f"Webhook received: {json.dumps(log_entry, ensure_ascii=False)}")


# Запуск приложения
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.APP_HOST,
        port=config.APP_PORT,
        log_level="info",
    )
