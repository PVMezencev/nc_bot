import asyncio
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header

from bots.general import GeneralBot, BOT_NAME_GENERAL
from bots.example import ExampleBot, BOT_NAME_EXAMPLE
from bots.scripts import ScriptsBot, BOT_NAME_SCRIPTS
from bots.document import DocumentBot, BOT_NAME_DOCUMENT

# Конфигурация
import config
from repo.mongo import Users

mongo_users_repo = Users(connection=config.MONGODB_CONNECTION)

# Глобальный state-контейнер для DocumentBot (mutable, чтобы избежать global внутри lifespan)
_state: dict = {}


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Lifespan-менеджер: старт и graceful shutdown."""
    # --- startup ---
    if config.DOCUMENT_CHAT_ROOM:
        doc_bot = DocumentBot(config.NEXTCLOUD_URL)
        doc_bot.start()
        _state["document_bot"] = doc_bot
        print(f"[main] DocumentBot запущен (папка: {config.DOCUMENT_WATCH_DIR}, "
              f"чат: {config.DOCUMENT_CHAT_ROOM}, интервал: {config.POLL_INTERVAL_SEC}с)")
    else:
        print("[main] DocumentBot отключён (DOCUMENT_CHAT_ROOM не задан)")

    yield

    # --- shutdown ---
    doc_bot = _state.get("document_bot")
    if doc_bot:
        doc_bot.stop()
        print("[main] DocumentBot остановлен")


app = FastAPI(
    title="Nextcloud Talk Bot",
    description="Бот для Nextcloud Talk",
    version="1.0.0",
    lifespan=lifespan,
)


# Обработчик вебхуков для bot_general
@app.post("/bots/{bot_name}")
async def handle_webhook(
        request: Request,
        bot_name: str,
        x_nextcloud_talk_signature: Optional[str] = Header(None, alias="X-Nextcloud-Talk-Signature"),
        x_nextcloud_talk_random: Optional[str] = Header(None, alias="X-Nextcloud-Talk-Random"),
):
    # Получаем тело запроса
    try:
        payload = await request.body()
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    print(f'payload: {payload}')
    print(f'data: {data}')

    # Инициализируем бота.
    if bot_name == BOT_NAME_GENERAL:
        bot = GeneralBot(config.NEXTCLOUD_URL, users_repo=mongo_users_repo)
    elif bot_name == BOT_NAME_EXAMPLE:
        bot = ExampleBot(config.NEXTCLOUD_URL)
    elif bot_name == BOT_NAME_SCRIPTS:
        bot = ScriptsBot(config.NEXTCLOUD_URL)
    elif bot_name == BOT_NAME_DOCUMENT:
        bot = DocumentBot(config.NEXTCLOUD_URL)
    else:
        raise HTTPException(status_code=404, detail=f"неизвестный бот {bot_name}")

    # Валидация подписи
    if not bot.verify_signature(
            payload,
            x_nextcloud_talk_signature,
            x_nextcloud_talk_random,
    ):
        print(f"❌ Неверная подпись!")
        print(f"   Random: {x_nextcloud_talk_random}")
        print(f"   Signature received: {x_nextcloud_talk_signature}")
        print(f"   Signature calculated: [скрыто]")
        raise HTTPException(status_code=401, detail="Invalid signature")

    # Логирование входящего запроса
    await bot.log_request(data)

    # Обработка сообщения
    response = await bot.process_message(data)

    if response:
        await bot.send_to_nextcloud(response.get('room_token'), response.get('message'))

    return response


# Запуск приложения
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config.APP_HOST,
        port=config.APP_PORT,
        log_level="info",
    )

    asyncio.run(mongo_users_repo.close())
