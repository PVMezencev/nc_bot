import asyncio
import os.path

from bots.common import Bot

BOT_NAME_SCRIPTS = "bot_scripts"


class ScriptsBot(Bot):
    def __init__(self, nc_url):
        self.bot_name = BOT_NAME_SCRIPTS  # Указываем имя, которе дали боту при регистрации.
        # Токен бота автоматически загрузится из botsecrets.py
        super().__init__(self.bot_name, nc_url)

        self.script_dir_name = "scripts_for_bot"
        self.script_dir = os.path.join(os.getcwd(), self.script_dir_name)

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
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            shell=True, executable='/bin/bash',
        )
        return await proc.communicate()

