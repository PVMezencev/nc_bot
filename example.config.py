import os
from dotenv import load_dotenv

load_dotenv()

# Конфигурация бота
BOT_NAME = os.getenv("BOT_NAME", "МойБот")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "your-secret-key-here")

# Nextcloud конфигурация
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL", "https://your-nextcloud.com")
NEXTCLOUD_TOKEN = os.getenv("NEXTCLOUD_TOKEN", "your-token-here")

# Настройки приложения
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# Список команд
COMMANDS = {
    "help": "Показать помощь",
    "привет": "Поздороваться",
    "время": "Текущее время",
    "погода [город]": "Прогноз погоды",
    "бот статус": "Статус бота"
}

# Время ответа
RESPONSE_TIMEOUT = 5  # секунд