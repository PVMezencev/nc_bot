import asyncio
import random
import re

import aiohttp
import json
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

from config import DEEPSEEK_TOKEN
from deepseek.deepseek import get_joke_request
from holidays import holidays_2026, holidays_2027

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ActorType(str, Enum):
    """Типы отправителей сообщений согласно документации"""
    USERS = "users"
    BOTS = "bots"
    GUESTS = "guests"
    DELETED_USERS = "deleted_users"
    BRIDGED = "bridged"
    UNKNOWN = "unknown"


@dataclass
class Message:
    """Класс для хранения информации о сообщении согласно документации API"""
    id: int
    token: str
    actor_type: str
    actor_id: str
    actor_display_name: str
    timestamp: int
    message: str
    message_type: str = "comment"
    system_message: str = ""
    is_replyable: bool = False
    reference_id: Optional[str] = None
    message_parameters: Dict = field(default_factory=dict)
    expiration_timestamp: Optional[int] = None
    reactions: Dict[str, int] = field(default_factory=dict)
    reactions_self: List[str] = field(default_factory=list)
    markdown: bool = False
    silent: bool = False

    @property
    def datetime(self) -> datetime:
        return datetime.fromtimestamp(self.timestamp)

    @property
    def is_from_bot(self) -> bool:
        return self.actor_type == ActorType.BOTS

    @property
    def is_from_user(self) -> bool:
        return self.actor_type == ActorType.USERS

    @property
    def is_system_message(self) -> bool:
        return bool(self.system_message)

    def __repr__(self) -> str:
        return f"Message(id={self.id}, from={self.actor_type}:{self.actor_id}, text={self.message[:50]})"


class NextcloudTalkBot:
    """Асинхронный бот для Nextcloud Talk согласно официальной документации"""

    def __init__(self, server_url: str, username: str, password: str, room_token: str):
        """
        Инициализация бота

        :param server_url: URL Nextcloud сервера (например, https://nextcloud.example.com)
        :param username: Имя пользователя
        :param password: Пароль или токен приложения
        :param room_token: Токен комнаты (из URL чата)
        """
        self.server_url = server_url.rstrip('/')
        self.username = username
        self.password = password
        self.room_token = room_token

        # Правильный API endpoint согласно документации
        self.base_api_url = f"{self.server_url}/ocs/v2.php/apps/spreed/api/v1"
        self.session: Optional[aiohttp.ClientSession] = None
        self.last_known_message_id: Optional[int] = None
        self.last_common_read_id: Optional[int] = None

        # Временная метка старта — чтобы не реагировать на старые сообщения при перезапуске
        self._start_time: Optional[datetime] = None

        # Кэш - отправлена ли шутка сегодня, чтоб не задолбать всех своими шутками.
        self.__date_joke = dict()

    async def __aenter__(self):
        """Поддержка async context manager"""
        self.session = aiohttp.ClientSession()

        # Настройка базовой авторизации
        self.session._default_auth = aiohttp.BasicAuth(self.username, self.password)

        # Важно: отправляем Accept: application/json для получения JSON вместо XML
        self.session.headers.update({
            'OCS-APIRequest': 'true',
            'Accept': 'application/json',
            'Content-Type': 'application/json'
        })

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Закрытие сессии при выходе из контекста"""
        if self.session:
            await self.session.close()

    async def get_chat_messages(
            self,
            look_into_future: int = 0,
            limit: int = 100,
            last_known_message_id: Optional[int] = None,
            last_common_read_id: Optional[int] = None,
            timeout: int = 30,
            set_read_marker: int = 1,
            include_last_known: int = 0,
            no_status_update: int = 0,
            mark_notifications_as_read: int = 1
    ) -> tuple[List[Message], Optional[int], Optional[int]]:
        """
        Получение сообщений из чата согласно документации API

        :param look_into_future: 1 - ожидать новые сообщения, 0 - получить историю
        :param limit: Количество сообщений (макс 200)
        :param last_known_message_id: ID последнего известного сообщения (оффсет)
        :param last_common_read_id: ID последнего прочитанного всеми сообщения
        :param timeout: Секунд ожидания новых сообщений (макс 60)
        :param set_read_marker: 1 - автоматически устанавливать маркер прочтения
        :param include_last_known: 1 - включить последнее известное сообщение
        :param no_status_update: 1 - не обновлять статус пользователя
        :param mark_notifications_as_read: 0 - не отмечать уведомления как прочитанные
        :return: (список сообщений, last_given_id, last_common_read_id)
        """
        params = {
            'lookIntoFuture': look_into_future,
            'limit': min(limit, 200),  # API ограничивает 200 сообщениями
            'setReadMarker': set_read_marker,
            'includeLastKnown': include_last_known,
            'noStatusUpdate': no_status_update,
            'markNotificationsAsRead': mark_notifications_as_read
        }

        if last_known_message_id:
            params['lastKnownMessageId'] = last_known_message_id

        if last_common_read_id:
            params['lastCommonReadId'] = last_common_read_id

        if look_into_future == 1:
            params['timeout'] = min(timeout, 60)  # API ограничивает 60 секундами

        try:
            async with self.session.get(
                    f"{self.base_api_url}/chat/{self.room_token}",
                    params=params
            ) as response:

                if response.status == 200:
                    data = await response.json()

                    # Получаем заголовки с оффсетами
                    last_given_id = response.headers.get('X-Chat-Last-Given')
                    last_common_read = response.headers.get('X-Chat-Last-Common-Read')

                    # Парсим сообщения
                    messages = []
                    if data.get('ocs', {}).get('data'):
                        for msg_data in data['ocs']['data']:
                            message = self._parse_message(msg_data)
                            if message:
                                messages.append(message)

                    return messages, last_given_id, last_common_read

                elif response.status == 304:
                    # Нет новых сообщений
                    logger.debug("Нет новых сообщений (304)")
                    return [], None, None

                elif response.status == 404:
                    logger.error(f"Комната {self.room_token} не найдена или нет доступа")
                    return [], None, None

                elif response.status == 412:
                    logger.error("Лобби активно, пользователь не модератор")
                    return [], None, None

                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка {response.status}: {error_text[:200]}")
                    return [], None, None

        except aiohttp.ClientError as e:
            logger.error(f"Ошибка запроса: {e}")
            return [], None, None
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка парсинга JSON: {e}")
            return [], None, None

    async def get_history(self, limit: int = 100) -> List[Message]:
        """Получение истории сообщений (lookIntoFuture=0)"""
        messages, _, _ = await self.get_chat_messages(
            look_into_future=0,
            limit=limit
        )
        return messages

    async def wait_for_new_messages(
            self,
            timeout: int = 30,
            last_known_message_id: Optional[int] = None
    ) -> List[Message]:
        """
        Ожидание новых сообщений (long polling)

        :param timeout: Максимальное время ожидания в секундах (до 60)
        :param last_known_message_id: ID последнего известного сообщения
        :return: Список новых сообщений
        """
        messages, last_given, _ = await self.get_chat_messages(
            look_into_future=1,
            timeout=timeout,
            last_known_message_id=last_known_message_id or self.last_known_message_id,
            set_read_marker=0  # Не устанавливаем маркер прочтения при ожидании
        )

        # Обновляем last_known_message_id если получили новые сообщения
        if messages and last_given:
            self.last_known_message_id = int(last_given)

        return messages

    async def send_message(
            self,
            message: str,
            reply_to: Optional[int] = None,
            reference_id: Optional[str] = None,
            silent: bool = False
    ) -> Optional[int]:
        """
        Отправка сообщения в чат

        :param message: Текст сообщения
        :param reply_to: ID сообщения, на которое отвечаем
        :param reference_id: Строка для идентификации сообщения
        :param silent: Отправить без звукового уведомления
        :return: ID отправленного сообщения или None
        """
        payload = {'message': message}

        if reply_to:
            payload['replyTo'] = reply_to
        if reference_id:
            payload['referenceId'] = reference_id
        if silent:
            payload['silent'] = 1

        try:
            logger.info(f"Отправляем сообщение в чат [{self.room_token}]: {json.dumps(payload, indent=2, ensure_ascii=False)}")
            async with self.session.post(
                    f"{self.base_api_url}/chat/{self.room_token}",
                    json=payload
            ) as response:

                if response.status in [200, 201]:
                    data = await response.json()
                    message_id = data.get('ocs', {}).get('data', {}).get('id')
                    logger.info(f"Сообщение отправлено (ID: {message_id}): {message[:50]}...")
                    return message_id
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка отправки {response.status}: {error_text[:200]}")
                    return None

        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {e}")
            return None

    def _parse_message(self, data: Dict[str, Any]) -> Optional[Message]:
        """Парсинг сообщения из API ответа"""
        try:
            return Message(
                id=data.get('id'),
                token=data.get('token', ''),
                actor_type=data.get('actorType', ActorType.UNKNOWN),
                actor_id=data.get('actorId', ''),
                actor_display_name=data.get('actorDisplayName', ''),
                timestamp=data.get('timestamp', 0),
                message=data.get('message', ''),
                message_type=data.get('messageType', 'comment'),
                system_message=data.get('systemMessage', ''),
                is_replyable=data.get('isReplyable', False),
                reference_id=data.get('referenceId'),
                message_parameters=data.get('messageParameters', {}),
                expiration_timestamp=data.get('expirationTimestamp'),
                reactions=data.get('reactions', {}),
                reactions_self=data.get('reactionsSelf', []),
                markdown=data.get('markdown', False),
                silent=data.get('silent', False)
            )
        except Exception as e:
            logger.error(f"Ошибка парсинга сообщения: {e}")
            return None

    async def mark_messages_as_read(self, last_read_message_id: int):
        """
        Отметить сообщения как прочитанные

        :param last_read_message_id: ID последнего прочитанного сообщения
        """
        params = {'lastReadMessage': last_read_message_id}

        try:
            async with self.session.post(
                    f"{self.base_api_url}/chat/{self.room_token}/read",
                    params=params
            ) as response:
                if response.status == 204:
                    logger.debug(f"Сообщения отмечены как прочитанные до ID {last_read_message_id}")
                else:
                    logger.warning(f"Не удалось отметить сообщения как прочитанные: {response.status}")
        except Exception as e:
            logger.error(f"Ошибка при отметке прочитанных: {e}")

    async def poll_messages(self, poll_interval: int = 3, callback=None, joke_day_callback=None):
        """
        Постоянный опрос новых сообщений с использованием long polling

        :param poll_interval: Интервал между запросами в секундах
        :param callback: Асинхронная функция для обработки сообщений
        """
        logger.info(f"Начинаем мониторинг чата {self.room_token}...")

        # Получаем последние сообщения для инициализации
        history = await self.get_history(limit=3)
        if history:
            self.last_known_message_id = max(msg.id for msg in history)
            logger.info(f"Инициализирован с последним ID: {self.last_known_message_id}")

        # Запоминаем время старта — не реагируем на сообщения, пришедшие до запуска бота
        self._start_time = datetime.now()
        logger.info(f"Время старта: {self._start_time.strftime('%Y-%m-%d %H:%M:%S')}")

        while True:
            if joke_day_callback:
                now = datetime.now()
                now_str = now.strftime("%Y-%m-%d")
                # Время рабочий день - пора отправить шутку.
                need_joke = 5 < now.hour < 14
                if need_joke:
                    # Если уже пора, проверим
                    # что не отправляли еще шутку сегодня.
                    need_joke = now_str not in self.__date_joke
                if need_joke:
                    # Если не отправляли, проверим
                    # что не пятница и не суббота.
                    need_joke = now.weekday() not in [5,6]
                if need_joke:
                    # Если не пятница и не суббота - проверим, не праздничный ли день 2026 года.
                    need_joke = now_str not in holidays_2026
                if need_joke:
                    # Если не пятница и не суббота, и не праздничный ли день 2026 года -
                    # проверим, не праздничный ли день 2027 года.
                    need_joke = now_str not in holidays_2027
                if need_joke:
                    # Вызываем функцию получения шутки дня!
                    try:
                        await joke_day_callback(now)
                        # Укажем, что шутка отправлена, и за одно сбросим кэш, чтоб память не потекла.
                        self.__date_joke = {
                            now_str: True
                        }
                    except Exception as e:
                        logger.error(f"Ошибка при выполнении joke_day_callback(): {e}")
            try:
                # Используем long polling для ожидания новых сообщений
                new_messages = await self.wait_for_new_messages(
                    timeout=30,
                    last_known_message_id=self.last_known_message_id
                )

                if new_messages:
                    logger.info(f"Получено {len(new_messages)} новых сообщений")

                    # Фильтрация: не реагировать на старые сообщения при перезапуске
                    # Обновляем last_known_message_id для всех сообщений, но обрабатываем
                    # через callback только те, что пришли после старта бота
                    processed_count = 0
                    for message in new_messages:
                        msg_time = message.datetime
                        if msg_time < self._start_time:
                            logger.debug(
                                f"⏭️ Пропущено старое сообщение (от {msg_time.strftime('%Y-%m-%d %H:%M:%S')}): "
                                f"{message.message[:60]}..."
                            )
                        else:
                            if callback:
                                await callback(message)
                            else:
                                await self.default_message_handler(message)
                            processed_count += 1

                    if processed_count > 0:
                        logger.info(f"Обработано {processed_count} новых сообщений (пришло {len(new_messages)})")

                    # Отмечаем сообщения как прочитанные (все, включая старые)
                    if new_messages:
                        await self.mark_messages_as_read(new_messages[-1].id)

                # Небольшая пауза между long polling запросами
                await asyncio.sleep(poll_interval)

            except asyncio.CancelledError:
                logger.info("Мониторинг остановлен")
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле опроса: {e}")
                await asyncio.sleep(poll_interval)

    async def default_message_handler(self, message: Message):
        """Обработчик сообщений по умолчанию"""
        time_str = message.datetime.strftime('%Y-%m-%d %H:%M:%S')

        if message.is_system_message:
            logger.info(f"[{time_str}] 📢 СИСТЕМА: {message.system_message}")
            return

        actor_icon = {
            ActorType.USERS: "👤",
            ActorType.BOTS: "🤖",
            ActorType.GUESTS: "👋",
            ActorType.DELETED_USERS: "💀"
        }.get(message.actor_type, "❓")

        display_name = message.actor_display_name or message.actor_id

        logger.info(f"[{time_str}] {actor_icon} {display_name} ({message.actor_type}): {message.message}")

        # Пример обработки команд от ботов
        if message.is_from_bot:
            if 'привет' in message.message.lower():
                await self.send_message(f"Привет, бот {display_name}! Я тебя слышу.")
            elif 'помощь' in message.message.lower():
                help_text = "Доступные команды:\n- привет\n- статус\n- боты"
                await self.send_message(help_text)
            elif 'статус' in message.message.lower():
                await self.send_message("✅ Бот работает, получает сообщения от всех, включая других ботов!")
            elif 'боты' in message.message.lower():
                await self.send_message("Да, я вижу сообщения от других ботов!")

    async def send_reaction_from_user(self, msg_id: str, chat: str, reaction_type: str = "like") -> dict:
        """
        Отправить реакцию (лайк или сердце) от имени конкретного пользователя

        Args:
            msg_id: ID сообщения (messageId)
            chat: ID чата (токен комнаты)
            reaction_type: Тип реакции ("like", "heart", "👍", "❤️", "😂", "🎉" и т.д.)

        Returns:
            dict: Ответ от API с данными о реакциях
        """

        # Преобразование типов реакций в эмодзи
        reaction_map = {
            # "like": "👍",
            # "heart": "❤️",
            # "laugh": "😂",
            # "wow": "😮",
            # "sad": "😢",
            # "party": "🎉",
            "fire": "🔥",
            "👍": "👍",
            "❤️": "❤️",
            # "😂": "😂",
            # "😮": "😮",
            # "😢": "😢",
            "🎉": "🎉",
            # "🔥": "🔥"
        }
        reaction = reaction_map.get(reaction_type, "👍")

        reaction_values = list(reaction_map.values())
        random_idx = random.randint(0, len(reaction_values) - 1)
        reaction = reaction_values[random_idx]

        headers = {
            'Content-Type': 'application/json',
            'OCS-APIRequest': 'true',
            'Accept': 'application/json'
        }

        # Правильный endpoint согласно документации
        url = f"{self.server_url}/ocs/v2.php/apps/spreed/api/v1/reaction/{chat}/{msg_id}"

        payload = {
            "reaction": reaction
        }
        # Убеждаемся, что сессия существует
        if self.session is None:
            self.session = aiohttp.ClientSession()

        try:
            async with self.session.post(
                    url,
                    auth=aiohttp.BasicAuth(self.username, self.password),
                    headers=headers,
                    json=payload
            ) as response:
                if response.status == 201:
                    # 201 Created - User reacted with a new reaction
                    result = await response.json()
                    print(f"✅ Новая реакция '{reaction}' к сообщению {msg_id}")
                    return {
                        "status": "created",
                        "data": result.get("ocs", {}).get("data", []),
                        "message": "Reaction added successfully"
                    }
                elif response.status == 200:
                    # 200 OK - Reaction already exists
                    result = await response.json()
                    print(f"ℹ️ Реакция '{reaction}' уже существует к сообщению {msg_id}")
                    return {
                        "status": "already_exists",
                        "data": result.get("ocs", {}).get("data", []),
                        "message": "Reaction already existed"
                    }
                elif response.status == 400:
                    error_text = await response.text()
                    print(f"❌ Bad Request (400): Нет поддержки реакций или сообщение вне контекста - {error_text}")
                    return {
                        "error": "bad_request",
                        "status_code": 400,
                        "details": "No reaction support or message out of reactions context",
                        "response": error_text
                    }
                elif response.status == 403:
                    error_text = await response.text()
                    print(f"❌ Forbidden (403): Пользователь не имеет прав на реакцию - {error_text}")
                    return {
                        "error": "forbidden",
                        "status_code": 403,
                        "details": "Participant does not have the required permission to react (need permission 256)",
                        "response": error_text
                    }
                elif response.status == 404:
                    error_text = await response.text()
                    print(f"❌ Not Found (404): Чат или сообщение не найдены - {error_text}")
                    return {
                        "error": "not_found",
                        "status_code": 404,
                        "details": "Conversation or message could not be found",
                        "response": error_text
                    }
                else:
                    error_text = await response.text()
                    print(f"❌ Неожиданная ошибка: {response.status} - {error_text}")
                    return {
                        "error": "unexpected_error",
                        "status_code": response.status,
                        "details": error_text
                    }

        except Exception as e:
            print(f"❌ Исключение при отправке реакции: {e}")
            return {"error": "exception", "details": str(e)}


def parse_total_amount(text)-> float | None:
    # Ищем "Итого" и затем число
    match = re.search(r'итого.*?(\d[\d\s]*\d)\s*руб', text)
    if match:
        amount = re.sub(r'[\s]', '', match.group(1))
        return float(amount)


async def main():
    from config import pvm_name, pvm_token, room, nextcloud_url

    async with NextcloudTalkBot(nextcloud_url, pvm_name, pvm_token, room) as bot:
        # Проверяем подключение получая историю
        logger.info("Проверка подключения...")
        history = await bot.get_history(limit=5)

        if history is not None:
            logger.info(f"✅ Успешное подключение! Получено {len(history)} сообщений из истории")

            # Показываем статистику по ботам в истории
            bot_messages = [msg for msg in history if msg.is_from_bot]
            if bot_messages:
                logger.info(f"🤖 Найдено сообщений от ботов: {len(bot_messages)}")
                for msg in bot_messages[:3]:
                    logger.info(f"  - Бот {msg.actor_display_name}: {msg.message[:80]}")
            else:
                logger.info("В истории нет сообщений от ботов")
        else:
            logger.error("❌ Не удалось подключиться к чату")
            return

        # Кастомный обработчик для сообщений от ботов
        async def custom_handler(message: Message):
            if message.is_from_bot:
                logger.info(f"🤖 ПОЛУЧЕНО ОТ БОТА [{message.actor_display_name}]: {message.message}")
                msg_text = message.message.lower()
                if 'поступила оплата' in msg_text or 'рублей,' in msg_text:
                    random_reaction_sleep = random.uniform(20, 40)
                    await asyncio.sleep(random_reaction_sleep)
                    await bot.send_reaction_from_user(f'{message.id}', room)

                total_amount = parse_total_amount(msg_text)
                logger.info(f"🤖 Итоговая сумма [{msg_text}]: {total_amount}")
                if total_amount:
                    txt = ''
                    if total_amount > 500000:
                        txt = 'Улёёёёёёт! 💪💪💪'
                    elif total_amount > 400000:
                        txt = '😍 Обалдеть!'
                    elif total_amount > 300000:
                        txt = 'Это офигенно! 👍'

                    logger.info(f"🤖 [{txt}]: {total_amount}")
                    if txt != '':
                        await bot.send_message(txt, reply_to=message.id)
            else:
                logger.debug(f"Сообщение от пользователя: {message.message[:50]}")

        async def joke_day(now: datetime):
            today = now.date()
            weekday = today.weekday()
            weekday_title = ''
            if weekday == 0:
                weekday_title = 'Понедельник'
            elif weekday == 1:
                weekday_title = 'Вторник'
            elif weekday == 2:
                weekday_title = 'Среда'
            elif weekday == 3:
                weekday_title = 'Четверг'
            elif weekday == 4:
                weekday_title = 'Пятница'
            elif weekday == 5:
                weekday_title = 'Суббота'
            elif weekday == 6:
                weekday_title = 'Воскресенье'
            joke = get_joke_request(ds_token=DEEPSEEK_TOKEN, date=today, weekday=weekday_title)

            await bot.send_message(joke)

        logger.info("🚀 Запуск мониторинга чата...")
        logger.info("Бот будет получать сообщения от ВСЕХ участников, включая других ботов")

        await bot.poll_messages(poll_interval=2, callback=custom_handler, joke_day_callback=joke_day)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен пользователем")
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
