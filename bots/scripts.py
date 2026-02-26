import asyncio
import json
import os.path
import shutil
from datetime import datetime
from pprint import pprint
from typing import Dict, Any

import config
from bots.common import Bot
from nextcloud.nextcloudapi import NextcloudClient
from utilites import unzip_archive

BOT_NAME_SCRIPTS = "bot_scripts"


class ScriptsBot(Bot):
    def __init__(self, nc_url):
        self.bot_name = BOT_NAME_SCRIPTS  # Указываем имя, которе дали боту при регистрации.
        # Токен бота автоматически загрузится из botsecrets.py
        super().__init__(self.bot_name, nc_url)

        self.script_dir_name = "scripts_for_bot"
        self.script_dir = os.path.join(os.getcwd(), self.script_dir_name)

    async def process_message(self, data: Dict[str, Any]) -> Dict[str, Any] | None:
        """Обработка входящего сообщения"""
        # Извлекаем данные
        try:
            message_json = data.get("object", {}).get("content", "").strip()
            message_obj = json.loads(message_json)
            message_text = message_obj.get("message", "")
            user_id_data = data.get("actor", {}).get("id", "")
            tails = user_id_data.split("/")
            if len(tails) > 1:
                user_id = tails[1]
            else:
                user_id = user_id_data
            room_token = data.get("target", {}).get("id", "")
            message_id = data.get("object", {}).get("id", "")

        except Exception as e:
            print(f'{datetime.now().isoformat("T")}: {e}')
            return

        message_text = message_text.strip()
        if message_text == "{file}":
            if user_id not in [
                        "3A5D0454-58BC-4A83-9744-BE34B4292471",  # Кульнев ПВ
                        "pvmezencev",
                    ]:
                return {
                    "message": f"Доступ запрещён",
                    "replyTo": message_id,
                    "room_token": room_token,
                    "silent": False
                }
            parameters = message_obj.get("parameters", {})
            file = parameters.get("file", {})
            pprint(file)
            file_path = file.get("path")
            api_client = NextcloudClient(config.NEXTCLOUD_URL, config.NEXTCLOUD_API_USER, config.NEXTCLOUD_API_PASSWORD)
            api_client.download_file(os.path.join("Talk", file_path), self.script_dir)

            file_mimetype = file.get("mimetype")
            if file_mimetype == "text/x-python":
                pass
            elif file_mimetype == "application/zip":
                zip_file = os.path.join(self.script_dir, file_path)
                err = unzip_archive(zip_file, self.script_dir)
                if err:
                    return {
                        "message": f"{err} {file_path}",
                        "replyTo": message_id,
                        "room_token": room_token,
                        "silent": False
                    }
                os.remove(zip_file)
                result = await self.__deploy(self.script_dir)
                return {
                    "message": f"результат развёртывания архива скриптов: {result}",
                    "replyTo": message_id,
                    "room_token": room_token,
                    "silent": False
                }

            return {
                "message": f"файл {file_path} сохранен в каталог {self.script_dir}",
                "replyTo": message_id,
                "room_token": room_token,
                "silent": False
            }

        # Игнорируем сообщения без текста или от ботов
        if not message_text or user_id.startswith("bot-"):
            return

        # Проверяем, обращено ли сообщение к боту
        bot_mentioned = (
                f"@{self.bot_name}" in message_text or
                self.bot_name in message_text or
                message_text.startswith("!")
        )

        if not bot_mentioned:
            return

        # Очищаем сообщение от упоминания бота
        clean_message = message_text.replace(f"@{self.bot_name}", "").replace(self.bot_name,
                                                                              "").strip().removeprefix(
            "!")

        # Обработка команд
        response_text = await self.handle_command(clean_message, user_id, room_token)

        # Формируем ответ
        if response_text:
            return {
                "message": response_text,
                "replyTo": message_id,
                "room_token": room_token,
                "silent": False
            }

    async def handle_command(self, message: str, user_id: str = None, room_token: str = None) -> str:
        params = message.split(" ")
        if len(params) == 0:
            return ""
        script_name = params[0]
        if not script_name.endswith(".py"):
            script_name = f'{script_name}.py'

        if script_name == "example.py":
            script_path = os.path.join(os.getcwd(), "scripts_examples", "example.py")
            cwd = os.path.join(os.getcwd(), "scripts_examples")
        else:
            script_path = os.path.join(self.script_dir, script_name)
            cwd = self.script_dir

        if not os.path.exists(script_path):
            return f"{script_path} - не найден в каталоге скриптов."

        python_interpreter = '.venv/bin/python'
        if not os.path.exists(python_interpreter):
            python_interpreter = 'python'

        proc = await asyncio.create_subprocess_exec(
            python_interpreter, '-u', script_name, " ".join(params[1:]),
            limit=1024 * 1024,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        response = ""
        while True:
            data = await proc.stdout.readline()
            if not data:
                err = await proc.stderr.readline()
                if err:
                    line = err.decode()
                    response += f"\nERR: {line}"
                    continue
                break
            line = data.decode()
            response += f"\n{line}"

        return response.strip()

    async def handle_unknown(self, command: str, user_id=None) -> str:
        """Неизвестная команда"""
        return f"❌ Неизвестная команда: `{command}`\nИспользуйте `!имя_скрипта [список аргументов через пробел]`."

    async def __run_cmd(self, cmd):
        print(f'run_cmd: {cmd}')
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True, executable='/bin/bash',
        )
        return await proc.communicate()

    async def __deploy(self, project_dir):
        venv_dir = os.path.join(project_dir, '.venv')
        if not os.path.exists(venv_dir):
            cmd = f'cd {project_dir} && python3 -m venv .venv && pip install --root-user-action=ignore -U pip && cd ..'
            stdout, stderr = await self.__run_cmd(cmd)
            if stderr:
                return [f'{stderr.decode().strip()}']

        cmd = f'cd {project_dir} && source .venv/bin/activate && pip install --root-user-action=ignore -r requirements.txt'
        stdout, stderr = await self.__run_cmd(cmd)

        if stdout:
            return [f'{stdout.decode().strip()}']
        if stderr:
            return [f'{stderr.decode().strip()}']
