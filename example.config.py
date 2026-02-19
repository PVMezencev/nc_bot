import os
from dotenv import load_dotenv

load_dotenv()

# Nextcloud конфигурация
NEXTCLOUD_URL = os.getenv("NEXTCLOUD_URL", "https://your-nextcloud.com")
NEXTCLOUD_API_USER = os.getenv("NEXTCLOUD_API_USER", "api_user")
NEXTCLOUD_API_PASSWORD = os.getenv("NEXTCLOUD_API_PASSWORD", "api_user_password")

MONGODB_CONNECTION = os.getenv("MONGODB_CONNECTION", "mongodb://administrator:example@mongo:27017/")

APP_HOST = os.getenv("LOG_LEVEL", "0.0.0.0")
APP_PORT = os.getenv("APP_PORT", 8000)