import csv
import io
from datetime import datetime
from typing import Dict, List

from docker.errors import APIError

import botsecrets
from bots.common import Bot, ChatState
from nextcloud.users import get_user_profile
from devops import containers


class GeneralState:
    awaited_bot_name = "awaited_bot_name"
    awaited_bot_token = "awaited_bot_token"
    awaited_bot_id = "awaited_bot_id"
    install_bot = "install_bot"


class GeneralBot(Bot):
    def __init__(self, nc_url):
        self.bot_name = "general_bot"
        self.bot_token = botsecrets.BOT_SECRETS.get(self.bot_name)
        self.state = GeneralState()
        super().__init__(self.bot_name, self.bot_token, nc_url)
        self.__GENERATE = "<generate>"
        self.__SUCCESS_INSTALL = "Bot installed"
        self.__SUCCESS_UNINSTALL = "Bot uninstalled"
        self.__HANDLER_FIELD = 'handler'
        self.__HELP_TEXT_FIELD = 'help'
        self.command_handlers = {
            "помощь": {
                self.__HANDLER_FIELD: self.handle_help,
                self.__HELP_TEXT_FIELD: "Вызов этой справки"
            },
            "привет": {
                self.__HANDLER_FIELD: self.handle_greet,
                self.__HELP_TEXT_FIELD: "Приветствие"
            },
            "время": {
                self.__HANDLER_FIELD: self.handle_time,
                self.__HELP_TEXT_FIELD: "Время на сервере"
            },
            "я": {
                self.__HANDLER_FIELD: self.handle_bot_user_profile,
                self.__HELP_TEXT_FIELD: "Мой профиль"
            },
            "список_ботов": {
                self.__HANDLER_FIELD: self.handle_list_bot,
                self.__HELP_TEXT_FIELD: "Список ботов"
            },
            "новый_бот": {
                self.__HANDLER_FIELD: self.handle_new_bot_request,
                self.__HELP_TEXT_FIELD: "Запрос на создание нового бота"
            },
            "удалить_бота": {
                self.__HANDLER_FIELD: self.handle_rm_bot_request,
                self.__HELP_TEXT_FIELD: "Запрос на удаление бота"
            },
        }

    async def handle_help(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Обработка команды помощи"""
        help_text = """
        🤖 *Доступные команды:*
"""
        for cmd, obj in self.command_handlers.items():
            desc = obj.get(self.__HELP_TEXT_FIELD)
            help_text += f'• `{cmd}` - {desc}\n'

        help_text += '\nОтправка команд боту через !\n'
        help_text += 'Если восклицательный знак не указан - бот игнорирует текст.\n'
        help_text += '*Например:*\n'
        help_text += '`!помощь` или `! помощь` (пробел после ! допускается)\n'
        return help_text

    async def handle_greet(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Приветствие"""
        return "Привет! 👋 Я бот ЗАО СММ. Напишите `!помощь` для списка команд."

    async def handle_time(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Текущее время"""
        now = datetime.now()
        return f"🕐 Текущее время: {now.strftime('%H:%M:%S %d.%m.%Y')} UTC"

    async def handle_weather(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Прогноз погоды"""
        if not command_args:
            return "Укажите город. Пример: `погода Москва`"

        city = ' '.join(command_args)
        # Здесь можно добавить вызов API погоды
        return f"🌤️ Погода для {city}: +18°C, солнечно (шутка)"

    async def handle_bot_status(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Статус бота"""
        return "✅ Бот работает нормально\nВерсия: 1.0.0\nВремя работы: 24/7"

    async def handle_bot_user_profile(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
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

    def __get_bot_list(self) -> List | None:
        # docker exec -u 33 nextcloud_app php occ talk:bot:list
        contaoner = containers.container_by_name('nextcloud_app')
        if not contaoner:
            raise Exception("контейнер Nexcloud не найден! Используйте прямой доступ к серверу.")
        try:
            res = contaoner.exec_run('php occ talk:bot:list', user="33", demux=True)
        except APIError as e:
            raise Exception(f'{e}')
        res_output = res.output
        if not res_output or not isinstance(res_output, tuple) or len(res_output) == 0:
            raise Exception("неизвестная ошибка")
        data_bytes = res_output[0]
        # keys = ['id', 'name', 'description', 'error_count', 'state', 'features']
        try:
            data_str = data_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raise Exception("неизвестная ошибка")

        data_str = data_str.replace('+----+-------------+--------------+-------------+-------+-------------------+\n',
                                    '')
        data_str = data_str.replace('+----+-------------+--------------+-------------+-------+-------------------+', '')

        csv_file = io.StringIO(data_str)
        reader = csv.DictReader(csv_file, delimiter='|')

        bots = []
        for row in reader:
            if not isinstance(row, dict):
                continue
            clean = dict()
            for k, v in row.items():
                new_key = k.strip()
                if new_key == '':
                    continue
                clean[k.strip()] = v.strip()
            bots.append(clean)
        return bots

    def __remove_bot(self, bot_name="", bot_id="") -> str | None:
        if bot_id == "":
            if bot_name == "":
                raise Exception("укажите имя бота или его ID")
            try:
                exists_bots = self.__get_bot_list()
            except Exception as e:
                raise Exception(f'{e}')
            if not exists_bots:
                raise Exception("неизвестная ошибка")
            for bot in exists_bots:
                if bot.get('name') == bot_name:
                    bot_id = bot.get('id')
                    break

        contaoner = containers.container_by_name('nextcloud_app')
        if not contaoner:
            raise Exception("контейнер Nexcloud не найден! Используйте прямой доступ к серверу.")
        try:
            cmd = f'php occ talk:bot:uninstall {bot_id}'
            res = contaoner.exec_run(cmd, user="33", demux=True)
        except APIError as e:
            raise Exception(f'{e}')

        res_output = res.output
        if not res_output or not isinstance(res_output, tuple) or len(res_output) == 0:
            raise Exception("неизвестная ошибка")
        print(f'res_output: {res_output}')
        data_bytes = res_output[0]
        print(f'data_bytes: {data_bytes}')
        err_bytes = res_output[1]
        print(f'err_bytes: {err_bytes}')
        # keys = ['id', 'name', 'description', 'error_count', 'state', 'features']
        try:
            data_str = data_bytes.decode('utf-8').strip()
        except UnicodeDecodeError:
            raise Exception("неизвестная ошибка")

        if data_str != self.__SUCCESS_UNINSTALL:
            raise Exception(data_str)
        # docker exec -u 33 nextcloud_app php occ talk:bot:uninstall bot_id
        return

    def __install_bot(self, bot_name, bot_token="") -> str | None:
        # docker exec -u 33 nextcloud_app php occ talk:bot:install bot_name bot_token https://cloud.zaosmm.ru/bots/bot_name "bot_name"
        print(f'bot_name: {bot_name}')
        print(f'bot_token: {bot_token}')
        contaoner = containers.container_by_name('nextcloud_app')
        if not contaoner:
            raise Exception("контейнер Nexcloud не найден! Используйте прямой доступ к серверу.")

        if bot_token == "":
            import secrets
            bot_token = secrets.token_hex(64)

        print(f'bot_token2: {bot_token}')

        try:
            cmd = f'php occ talk:bot:install {bot_name} {bot_token} https://cloud.zaosmm.ru/bots/{bot_name} "{bot_name}"'
            res = contaoner.exec_run(cmd, user="33", demux=True)
        except APIError as e:
            raise Exception(f'{e}')
        res_output = res.output
        if not res_output or not isinstance(res_output, tuple) or len(res_output) == 0:
            raise Exception("неизвестная ошибка")
        print(f'res_output: {res_output}')
        data_bytes = res_output[0]
        print(f'data_bytes: {data_bytes}')
        err_bytes = res_output[1]
        print(f'err_bytes: {err_bytes}')
        # keys = ['id', 'name', 'description', 'error_count', 'state', 'features']
        try:
            data_str = data_bytes.decode('utf-8').strip()
        except UnicodeDecodeError:
            raise Exception("неизвестная ошибка")

        if data_str != self.__SUCCESS_INSTALL:
            raise Exception(data_str)

        return bot_token

    async def handle_list_bot(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        bots = self.__get_bot_list()

        text = """
                🤖 *Доступные боты:*
        """
        for b in bots:
            text += f'`{b.get("id")}`:\t{b.get("name")}\n'
        return text

    async def handle_new_bot_request(self, command_args: list = None, user_id=None, room_token: str = None):
        await ChatState.set_state(user_id, self.state.awaited_bot_name)
        return "Укажите имя для нового бота, например my_new_bot (через ! - !my_new_bot)"

    async def handle_rm_bot_request(self, command_args: list = None, user_id=None, room_token: str = None):
        await ChatState.set_state(user_id, self.state.awaited_bot_id)
        return "Укажите идентификатор бота для удаления, например !5"

    async def handle_command(self, message: str, user_id: str = None, room_token: str = None) -> str:
        """Обработка команд"""

        # Извлекаем команду и аргументы
        parts = message.split()
        if not parts:
            return ""

        command = parts[0].lower().strip()
        args = parts[1:] if len(parts) > 1 else []

        current_state = await ChatState.get_state(user_id)
        if current_state:
            current_data = await ChatState.get_data(user_id)
            if not current_data:
                current_data = {}
            if current_state == self.state.awaited_bot_name:
                if command == "":
                    return "Укажите имя для нового бота, например my_new_bot"
                current_data['bot_name'] = command
                await ChatState.set_state(user_id, self.state.awaited_bot_token)
                await ChatState.set_data(user_id, current_data)
                return (f"Укажите токен для нового бота {command} - случайный набор символов длиной 64-128 символов.\n"
                        f"Или отправьте !{self.__GENERATE} для генерации автоматически.")
            elif current_state == self.state.awaited_bot_token:
                new_bot_token = command
                print(f'received new_bot_token: {new_bot_token}')
                if new_bot_token == self.__GENERATE:
                    new_bot_token = ""
                new_bot_name = current_data.get("bot_name")
                if not new_bot_name or new_bot_name == "":
                    await ChatState.set_state(user_id, self.state.awaited_bot_name)
                    return "Укажите имя для нового бота, например my_new_bot"

                await ChatState.set_state(user_id, self.state.install_bot)
                await ChatState.set_data(user_id, current_data)
                await self.send_to_nextcloud(room_token, "Выполняю регистрацию нового бота...")
                try:
                    new_bot_token = self.__install_bot(new_bot_name, new_bot_token)
                except Exception as e:
                    return f'Ошибка: {e}'
                await ChatState.clear(user_id)
                return f"Новый бот зарегистрирован!\n```\n{new_bot_token}\n```\nСохраните токен."
            elif current_state == self.state.awaited_bot_id:

                await self.send_to_nextcloud(room_token, "Выполняю удаление бота...")
                try:
                    self.__remove_bot(bot_id=command)
                except Exception as e:
                    return f'Ошибка: {e}'
                await ChatState.clear(user_id)
                return f"Бот удален."

        # Выполняем команду
        handler = self.command_handlers.get(command).get('handler')
        if handler:
            return await handler(args, user_id, room_token)
        else:
            # Проверяем комбинированные команды типа "бот статус"
            if command == "бот" and args:
                sub_command = args[0].lower()
                if sub_command == "статус":
                    return await self.handle_bot_status(args[1:])

            return await self.handle_unknown(command)
