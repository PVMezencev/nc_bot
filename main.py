from typing import Optional

from fastapi import FastAPI, Request, HTTPException, Header

from bots.general import GeneralBot
# Конфигурация
import config

app = FastAPI(
    title="Nextcloud Talk Bot",
    description="Бот для Nextcloud Talk",
    version="1.0.0"
)


# Обработчик вебхуков для bot_general
@app.post("/bots/bot_general")
async def handle_webhook(
        request: Request,
        x_nextcloud_talk_signature: Optional[str] = Header(None, alias="X-Nextcloud-Talk-Signature"),
        x_nextcloud_talk_random: Optional[str] = Header(None, alias="X-Nextcloud-Talk-Random"),
):
    # Получаем тело запроса
    try:
        payload = await request.body()
        data = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {str(e)}")

    # Инициализируем бота.
    bot = GeneralBot()
    # Валидация подписи
    if config.WEBHOOK_SECRET:
        if not bot.verify_signature(
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
