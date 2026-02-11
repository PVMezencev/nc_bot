import hashlib
import hmac
import json
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
        secret: str = config.WEBHOOK_SECRET
) -> bool:
    """Проверка HMAC подписи от Nextcloud"""
    if not signature:
        return False

    expected_signature = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(signature, expected_signature)


# Обработка команд
class CommandHandler:
    @staticmethod
    async def handle_help(command_args: list = None) -> str:
        """Обработка команды помощи"""
        help_text = """
        🤖 *Доступные команды:*

        *Основные:*
        • `помощь` - показать это сообщение
        • `привет` - поздороваться с ботом
        • `время` - текущее время

        *Полезные:*
        • `погода [город]` - прогноз погоды
        • `курс [валюта]` - курс валюты
        • `перевод [текст]` - перевод текста

        *Администрирование:*
        • `бот статус` - статус бота
        • `бот пользователи` - список пользователей
        • `бот команды` - все команды

        *Формат:* `@ИмяБота команда параметры`
        """
        return help_text

    @staticmethod
    async def handle_greet(command_args: list = None) -> str:
        """Приветствие"""
        return "Привет! 👋 Я бот Nextcloud Talk. Напишите `помощь` для списка команд."

    @staticmethod
    async def handle_time(command_args: list = None) -> str:
        """Текущее время"""
        now = datetime.now()
        return f"🕐 Текущее время: {now.strftime('%H:%M:%S %d.%m.%Y')}"

    @staticmethod
    async def handle_weather(command_args: list = None) -> str:
        """Прогноз погоды"""
        if not command_args:
            return "Укажите город. Пример: `погода Москва`"

        city = ' '.join(command_args)
        # Здесь можно добавить вызов API погоды
        return f"🌤️ Погода для {city}: +18°C, солнечно"

    @staticmethod
    async def handle_bot_status(command_args: list = None) -> str:
        """Статус бота"""
        return "✅ Бот работает нормально\nВерсия: 1.0.0\nВремя работы: 24/7"

    @staticmethod
    async def handle_unknown(command: str) -> str:
        """Неизвестная команда"""
        return f"❌ Неизвестная команда: `{command}`\nИспользуйте `помощь` для списка команд."


# Основной обработчик вебхуков
@app.post("/bots/{bot_name}")
async def handle_webhook(
        bot_name: str,
        request: Request,
        x_nextcloud_talk_signature: Optional[str] = Header(None),
        x_nextcloud_talk_random: Optional[str] = Header(None)
):
    """Основной endpoint для вебхуков Nextcloud Talk"""

    # Получаем тело запроса
    try:
        payload = await request.body()
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")


    print(bot_name)
    print(data)
    # Валидация подписи
    if config.WEBHOOK_SECRET:
        if not verify_signature(payload, x_nextcloud_talk_signature):
            raise HTTPException(status_code=401, detail="Invalid signature")

    # Логирование входящего запроса
    await log_request(data)

    # Обработка сообщения
    response = await process_message(data)

    return response


async def process_message(data: Dict[str, Any]) -> Dict[str, Any]:
    """Обработка входящего сообщения"""

    # Извлекаем данные
    message_text = data.get("message", {}).get("message", "").strip()
    user_id = data.get("user", {}).get("id", "")
    room_token = data.get("room", {}).get("token", "")
    message_id = data.get("message", {}).get("id", "")

    # Игнорируем сообщения без текста или от ботов
    if not message_text or user_id.startswith("bot-"):
        return {}

    # Проверяем, обращено ли сообщение к боту
    bot_mentioned = (
            f"@{config.BOT_NAME}" in message_text or
            config.BOT_NAME in message_text or
            message_text.startswith("!")
    )

    if not bot_mentioned:
        return {}

    # Очищаем сообщение от упоминания бота
    clean_message = message_text.replace(f"@{config.BOT_NAME}", "").replace(config.BOT_NAME, "").strip()

    # Обработка команд
    response_text = await handle_command(clean_message, user_id, room_token)

    # Формируем ответ
    if response_text:
        return {
            "response": response_text,
            "replyTo": message_id,
            "silent": False
        }

    return {}


async def handle_command(message: str, user_id: str, room_token: str) -> str:
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
    }

    # Выполняем команду
    handler = command_handlers.get(command)
    if handler:
        return await handler(args)
    else:
        # Проверяем комбинированные команды типа "бот статус"
        if command == "бот" and args:
            sub_command = args[0].lower()
            if sub_command == "статус":
                return await CommandHandler.handle_bot_status(args[1:])

        return await CommandHandler.handle_unknown(command)


# Функция для отправки сообщений в Nextcloud
async def send_to_nextcloud(room_token: str, message: str):
    """Отправка сообщения в Nextcloud Talk"""
    url = f"{config.NEXTCLOUD_URL}/ocs/v2.php/apps/spreed/api/v1/chat/{room_token}"

    headers = {
        "OCS-APIRequest": "true",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config.NEXTCLOUD_TOKEN}"
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(
                url,
                json={"message": message},
                headers=headers,
                timeout=30.0
            )
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error sending to Nextcloud: {e}")
            return False


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
        host="0.0.0.0",
        port=8080,
        log_level="info",
    )
