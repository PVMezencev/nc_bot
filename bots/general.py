from datetime import datetime

import botsecrets
from bots.common import Bot
from nextcloud.users import get_user_profile


class GeneralBot(Bot):
    def __init__(self, nc_url):
        self.bot_name = "general_bot"
        self.bot_token = botsecrets.BOT_SECRETS.get(self.bot_name)
        super().__init__(self.bot_name, self.bot_token, nc_url)

    async def handle_help(self, command_args: list = None, user_id=None) -> str:
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

    async def handle_greet(self, command_args: list = None, user_id=None) -> str:
        """Приветствие"""
        return "Привет! 👋 Я бот ЗАО СММ. Напишите `!помощь` для списка команд."

    async def handle_time(self, command_args: list = None, user_id=None) -> str:
        """Текущее время"""
        now = datetime.now()
        return f"🕐 Текущее время: {now.strftime('%H:%M:%S %d.%m.%Y')} UTC"

    async def handle_weather(self, command_args: list = None, user_id=None) -> str:
        """Прогноз погоды"""
        if not command_args:
            return "Укажите город. Пример: `погода Москва`"

        city = ' '.join(command_args)
        # Здесь можно добавить вызов API погоды
        return f"🌤️ Погода для {city}: +18°C, солнечно (шутка)"

    async def handle_bot_status(self, command_args: list = None, user_id=None) -> str:
        """Статус бота"""
        return "✅ Бот работает нормально\nВерсия: 1.0.0\nВремя работы: 24/7"

    async def handle_bot_user_profile(self, command_args: list = None, user_id=None) -> str:
        res = await get_user_profile(user_id)
        ocs = res.get('ocs')
        profile = ocs.get('data')
        manager = profile.get('manager')
        email = profile.get('email')
        displayname = profile.get('displayname')
        organisation = profile.get('organisation')
        role = profile.get('role')
        return ("👤 Мой профиль\n"
                f"Имя: {displayname}\n"
                f"Подразделение: {organisation}\n"
                f"Руководитель: {manager}\n"
                f"Должность: {role}\n"
                f"Почта: {email}")

    async def handle_command(self, message: str, user_id: str = None, room_token: str = None) -> str:
        """Обработка команд"""

        # Извлекаем команду и аргументы
        parts = message.split()
        if not parts:
            return ""

        command = parts[0].lower()
        args = parts[1:] if len(parts) > 1 else []

        # Маппинг команд
        command_handlers = {
            "помощь": self.handle_help,
            "help": self.handle_help,
            "привет": self.handle_greet,
            "hello": self.handle_greet,
            "время": self.handle_time,
            "time": self.handle_time,
            "погода": self.handle_weather,
            "weather": self.handle_weather,
            "бот": self.handle_bot_status,
            "bot": self.handle_bot_status,
            "status": self.handle_bot_status,
            "me": self.handle_bot_user_profile,
            "я": self.handle_bot_user_profile,
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
                    return await self.handle_bot_status(args[1:])

            return await self.handle_unknown(command)
