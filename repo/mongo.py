import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List

from pymongo.errors import PyMongoError
import motor.motor_asyncio

from nextcloud import users as nc_users


class Users:
    def __init__(self, connection: str):
        self.db_name = "cloud_users"

        self.__client = motor.motor_asyncio.AsyncIOMotorClient(connection)
        self.__repo = self.__client[self.db_name]
        self.__collection_users = self.__repo["users"]

        # Создаем индексы для оптимизации запросов
        self._lock = asyncio.Lock()  # Блокировка для потокобезопасности
        self._sync_task = None  # Для отслеживания фоновой задачи

    async def initialize(self):
        """Инициализация индексов и запуск фоновой синхронизации"""
        await self._create_indexes()
        await self._start_background_sync()

    async def _create_indexes(self):
        """Создание необходимых индексов"""
        try:
            # Индекс для поля id (если используете кастомный id)
            await self.__collection_users.create_index("id", unique=True)
            # Индекс для поля email
            await self.__collection_users.create_index("email")
            # Индекс для поля updated_at для сортировки
            await self.__collection_users.create_index("updated_at")
        except PyMongoError as e:
            print(f"Ошибка при создании индексов: {e}")

    def _user_has_changed(self, current_user: Dict, new_user: Dict) -> bool:
        """
        Проверка, изменились ли данные пользователя

        Args:
            current_user: текущие данные пользователя
            new_user: новые данные пользователя

        Returns:
            bool: True если данные изменились
        """
        # Исключаем служебные поля из сравнения
        exclude_fields = {'_id', 'updated_at', 'created_at'}
        current_filtered = {k: v for k, v in current_user.items() if k not in exclude_fields}
        new_filtered = {k: v for k, v in new_user.items() if k not in exclude_fields}

        return current_filtered != new_filtered

    async def _fetch_external_users(self) -> List[Dict[str, Any]]:
        try:
            return await nc_users.users()
        except Exception as e:
            print(f'nextcloud.users(): {e}')
            return []

    async def sync_users(self):
        """
        Синхронизация списка пользователей с внешним источником
        """
        async with self._lock:  # Блокируем доступ к коллекции во время синхронизации
            try:
                # Получаем данные из внешнего источника
                external_users = await self._fetch_external_users()

                if not external_users:
                    print("Не получены данные от внешнего источника")
                    return

                # Получаем текущих пользователей из БД
                current_users = await self.__collection_users.find().to_list(length=None)
                current_users_dict = {str(user.get('id', user.get('_id'))): user for user in current_users}

                # Подготавливаем операции для bulk write
                bulk_operations = []
                current_time = datetime.utcnow()

                for ext_user in external_users:
                    user_id = ext_user.get('id')
                    if not user_id:
                        continue

                    # Добавляем временные метки
                    ext_user['updated_at'] = current_time

                    if user_id in current_users_dict:
                        # Проверяем, изменились ли данные
                        if self._user_has_changed(current_users_dict[user_id], ext_user):
                            bulk_operations.append(
                                self.__collection_users.update_one(
                                    {'id': user_id},
                                    {'$set': ext_user}
                                )
                            )
                    else:
                        # Новый пользователь
                        ext_user['created_at'] = current_time
                        bulk_operations.append(
                            self.__collection_users.insert_one(ext_user)
                        )

                # Выполняем массовые операции
                if bulk_operations:
                    # В Motor нет прямого bulk_write, используем asyncio.gather
                    await asyncio.gather(*bulk_operations)

                # Удаляем пользователей, которых нет во внешнем источнике
                external_ids = {user.get('id') for user in external_users if user.get('id')}
                await self.__collection_users.delete_many({'id': {'$nin': list(external_ids)}})

                print(f"Синхронизация завершена. Обновлено: {len(bulk_operations)}")

            except PyMongoError as e:
                print(f"Ошибка MongoDB при синхронизации: {e}")
                raise
            except Exception as e:
                print(f"Неожиданная ошибка при синхронизации: {e}")
                raise

    async def _start_background_sync(self):
        """Запуск фоновой задачи синхронизации"""

        async def sync_loop():
            while True:
                try:
                    await self.sync_users()
                    print("Синхронизация пользователей завершена")
                except Exception as e:
                    print(f"Ошибка при синхронизации: {e}")

                # Ждем 1 час до следующей синхронизации
                await asyncio.sleep(3600)

        # Запускаем фоновую задачу
        self._sync_task = asyncio.create_task(sync_loop())

    async def get_users(self, skip: int = 0, limit: int = 500, **filters) -> List[Dict[str, Any]]:
        """
        Получение списка пользователей с пагинацией и фильтрацией
        
        Args:
            skip: количество пропускаемых записей
            limit: максимальное количество записей
            **filters: фильтры для поиска
            
        Returns:
            List[Dict]: список пользователей
        """
        try:
            cursor = self.__collection_users.find(filters).skip(skip).limit(limit)
            return await cursor.to_list(length=limit)
        except PyMongoError as e:
            print(f"Ошибка при получении списка пользователей: {e}")
            return []

    async def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Получение пользователя по ID
        
        Args:
            user_id: идентификатор пользователя
            
        Returns:
            Optional[Dict]: данные пользователя или None
        """
        try:
            return await self.__collection_users.find_one({'id': user_id})
        except PyMongoError as e:
            print(f"Ошибка при получении пользователя {user_id}: {e}")
            return None

    async def create_user(self, user_data: Dict[str, Any]) -> Optional[str]:
        """
        Создание нового пользователя

        Args:
            user_data: данные пользователя

        Returns:
            Optional[str]: ID созданного пользователя или None
        """
        async with self._lock:
            try:
                user_data['created_at'] = datetime.now()
                user_data['updated_at'] = datetime.now()

                result = await self.__collection_users.insert_one(user_data)
                return str(result.inserted_id)
            except PyMongoError as e:
                print(f"Ошибка при создании пользователя: {e}")
                return None

    async def update_user(self, user_id: str, user_data: Dict[str, Any]) -> bool:
        """
        Обновление данных пользователя

        Args:
            user_id: идентификатор пользователя
            user_data: новые данные

        Returns:
            bool: успешность операции
        """
        async with self._lock:
            try:
                user_data['updated_at'] = datetime.now()

                result = await self.__collection_users.update_one(
                    {'id': user_id},
                    {'$set': user_data}
                )
                return result.modified_count > 0
            except PyMongoError as e:
                print(f"Ошибка при обновлении пользователя {user_id}: {e}")
                return False

    async def delete_user(self, user_id: str) -> bool:
        """
        Удаление пользователя

        Args:
            user_id: идентификатор пользователя

        Returns:
            bool: успешность операции
        """
        async with self._lock:
            try:
                result = await self.__collection_users.delete_one({'id': user_id})
                return result.deleted_count > 0
            except PyMongoError as e:
                print(f"Ошибка при удалении пользователя {user_id}: {e}")
                return False

    async def count_users(self, **filters) -> int:
        """
        Подсчет количества пользователей

        Args:
            **filters: фильтры для подсчета

        Returns:
            int: количество пользователей
        """
        try:
            return await self.__collection_users.count_documents(filters)
        except PyMongoError as e:
            print(f"Ошибка при подсчете пользователей: {e}")
            return 0

    async def close(self):
        """Закрытие соединений и остановка фоновых задач"""
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass

        self.__client.close()

    async def search_users(self, like_name=None):
        if like_name:
            users = await self.get_users(skip=0, limit=500, displayname={"$regex": like_name, "$options": "i"}, )
        else:
            users = await self.get_users()
        await self.close()
        return users

    async def user(self, id):
        user = await self.get_user(id)
        await self.close()
        return user
