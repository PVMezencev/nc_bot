from datetime import datetime

from bots.common import Bot


class ExampleState:
    awaited_bot_name = "awaited_example"


EXAMPLE_BOT = "example_bot"


class ExampleBot(Bot):
    def __init__(self, nc_url):
        self.bot_name = EXAMPLE_BOT  # Указываем имя, которе дали боту при регистрации.
        # Токен бота автоматически загрузится из botsecrets.py
        super().__init__(self.bot_name, nc_url)

        self.state = ExampleState()
        self.command_handlers = {
            "помощь": {
                self.HANDLER_FIELD: self.handle_help,
                self.HELP_TEXT_FIELD: "Вызов этой справки"
            },
            "привет": {
                self.HANDLER_FIELD: self.handle_greet,
                self.HELP_TEXT_FIELD: "Приветствие"
            },
            "время": {
                self.HANDLER_FIELD: self.handle_time,
                self.HELP_TEXT_FIELD: "Время на сервере"
            },
            "время_по_доступу": {
                self.HANDLER_FIELD: self.handle_time,
                self.HELP_TEXT_FIELD: "Время на сервере только определенным сотруникам",
                self.ACCESS_FIELD: [
                    "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                    "pvmezencev",
                ]
            },
        }

    async def handle_help(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Обработка команды помощи"""
        help_text = """
        🤖 *Доступные команды:*
"""
        for cmd, obj in self.command_handlers.items():
            desc = obj.get(self.HELP_TEXT_FIELD)
            help_text += f'• `{cmd}` - {desc}\n'

        help_text += '\nОтправка команд боту через !\n'
        help_text += 'Если восклицательный знак не указан - бот игнорирует текст.\n'
        help_text += '*Например:*\n'
        help_text += '`!помощь` или `! помощь` (пробел после ! допускается)\n'
        help_text += '\nЕсли запускаем сценарий, то последующие требуемые ботом данные тоже нужно отправлять через !\n'
        return help_text

    async def handle_greet(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Приветствие"""
        return "Привет! 👋 Я бот ЗАО СММ. Напишите `!помощь` для списка команд."

    async def handle_time(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Текущее время"""
        now = datetime.now()
        return f"🕐 Текущее время: {now.strftime('%H:%M:%S %d.%m.%Y')} UTC"
