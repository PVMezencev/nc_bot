import csv
import io
import json
import sys
from typing import List

from docker.errors import APIError

from bots.common import Bot, ChatState
from nextcloud.users import get_user_profile
from devops import containers
from repo.mongo import Users


class GeneralState:
    awaited_bot_name = "awaited_bot_name"
    awaited_bot_token = "awaited_bot_token"
    awaited_bot_id = "awaited_bot_id"
    install_bot = "install_bot"
    awaited_user_name = "awaited_user_name"


class GeneralBot(Bot):
    def __init__(self, nc_url, users_repo: Users):
        self.bot_name = "general_bot"
        super().__init__(self.bot_name, nc_url)

        self.state = GeneralState()
        self.users_repo = users_repo
        self.__GENERATE = "<generate>"
        self.__SUCCESS_INSTALL = "Bot installed"
        self.__SUCCESS_UNINSTALL = "Bot uninstalled"
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
            "я": {
                self.HANDLER_FIELD: self.handle_bot_user_profile,
                self.HELP_TEXT_FIELD: "Мой профиль"
            },
            "список_ботов": {
                self.HANDLER_FIELD: self.handle_list_bot,
                self.HELP_TEXT_FIELD: "Список ботов",
                self.ACCESS_FIELD: [
                    "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                    "pvmezencev",
                ]
            },
            "новый_бот": {
                self.HANDLER_FIELD: self.handle_new_bot_request,
                self.HELP_TEXT_FIELD: "Запускает сценарий регистрации нового бота",
                self.ACCESS_FIELD: [
                    "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                    "pvmezencev",
                ]
            },
            "удалить_бота": {
                self.HANDLER_FIELD: self.handle_rm_bot_request,
                self.HELP_TEXT_FIELD: "Запускает сценарий удаление бота",
                self.ACCESS_FIELD: [
                    "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                    "pvmezencev",
                ]
            },
            "сотрудник": {
                self.HANDLER_FIELD: self.handle_search_users_request,
                self.HELP_TEXT_FIELD: "Запускает сценарий поиска сотрудника",
                self.ACCESS_FIELD: [
                    "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                    "pvmezencev",
                ]
            },
            "перезапуск": {
                self.HANDLER_FIELD: self.handle_restart_request,
                self.HELP_TEXT_FIELD: "Выключает бота (запуск автоматически)",
                self.ACCESS_FIELD: [
                    "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                    "pvmezencev",
                ]
            },
            "сброс": {
                self.HANDLER_FIELD: self.handle_clean_state_request,
                self.HELP_TEXT_FIELD: "Отменяет сценарии",
            },
        }

    async def handle_state(self, user_id, room_token, command) -> str | None:

        current_state = await ChatState.get_state(user_id)
        if current_state:
            # Если запущен сценарий - отрабатываем его.
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

                self.__add_bot_to_secret(bot_name=new_bot_name, bot_token=new_bot_token)

                return f"Новый бот зарегистрирован!\n```\n{new_bot_token}\n```\nСохраните токен."
            elif current_state == self.state.awaited_bot_id:

                await self.send_to_nextcloud(room_token, "Выполняю удаление бота...")

                deleted_bot = self.__get_bot(bot_id=command)
                if not deleted_bot:
                    return "Бот не найден"
                bot_name = deleted_bot.get("name")
                try:
                    self.__remove_bot(bot_name=bot_name, bot_id=command)
                except Exception as e:
                    return f'Ошибка: {e}'

                self.__del_bot_from_secret(bot_name)

                await ChatState.clear(user_id)
                return f"Бот удален."

            elif current_state == self.state.awaited_user_name:

                await self.send_to_nextcloud(room_token, "🔎 Выполняю поиск сотрудника...")
                try:
                    users = await self.users_repo.search_users(command)
                except Exception as e:
                    return f'Ошибка: {e}'
                await ChatState.clear(user_id)

                text = """
                ✍ *Найденные сотрудники:*
        """
                for u in users:
                    text += f'ФИО:\t{u.get("displayname")}\n'
                    text += f'Телефон:\t{u.get("phone")}\n'
                    text += f'Руководитель:\t{u.get("manager")}\n'
                    text += f'Группы:\t{",".join(u.get("groups"))}\n'
                    text += f'ID:\t`{u.get("id")}`\n'
                return text

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

    def __get_bot(self, bot_name="", bot_id="") -> dict | None:
        try:
            exists_bots = self.__get_bot_list()
        except Exception as e:
            raise Exception(f'{e}')
        if not exists_bots:
            raise Exception("неизвестная ошибка")

        for bot in exists_bots:
            if bot.get('name') == bot_name:
                return bot
            if bot.get('id') == f'{bot_id}':
                return bot

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

    async def handle_search_users_request(self, command_args: list = None, user_id=None, room_token: str = None):
        await ChatState.set_state(user_id, self.state.awaited_user_name)
        return "Укажите часть имени пользователя (через ! - !иванов)"

    async def handle_restart_request(self, command_args: list = None, user_id=None, room_token: str = None):

        await self.send_to_nextcloud(room_token, "⚠ Перезапуск бота!")
        sys.exit(0)

    async def handle_clean_state_request(self, command_args: list = None, user_id=None, room_token: str = None):
        await ChatState.clear(user_id)
        return "Сценарии отменены"

    def __update_botssecret(self, bots: dict):
        try:
            with open('botsecrets.py', 'w') as secret_writer:
                secret_writer.write(f"BOT_SECRETS = {json.dumps(bots, ensure_ascii=False)}")
        except:
            pass

    def __add_bot_to_secret(self, bot_name, bot_token):
        if not bot_name or bot_name == "":
            return
        if not bot_token or bot_token == "":
            return
        import botsecrets
        bots = botsecrets.BOT_SECRETS.copy()
        bots[bot_name] = bot_token

        self.__update_botssecret(bots)

    def __del_bot_from_secret(self, bot_name):
        if not bot_name or bot_name == "":
            return
        import botsecrets
        bots = botsecrets.BOT_SECRETS.copy()
        try:
            del bots[bot_name]
        except KeyError:
            return

        self.__update_botssecret(bots)
