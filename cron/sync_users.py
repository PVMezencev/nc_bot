import asyncio

import config
from repo.mongo import Users


async def main():
    users_manager = Users(
        connection=config.MONGODB_CONNECTION,
    )
    # Запускаем фоновую синхронизацию
    await users_manager.initialize()

    await users_manager.sync_users()

    # Очистка при завершении
    if users_manager:
        await users_manager.close()


asyncio.run(main())
