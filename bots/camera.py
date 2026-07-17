"""
CameraBot — захват кадров с IP-камер по RTSP через ffmpeg
и отправка скриншотов в чат Nextcloud Talk.
"""

import asyncio
import shutil
from datetime import datetime

import config
from bots.common import Bot

BOT_NAME_CAMERA = "bot_camera"


CAMERAS = {
    "разработчики": {
        "channel": "101",
        "label": "Разработчики",
        "emoji": "💻",
    },
    "коридор": {
        "channel": "201",
        "label": "Коридор",
        "emoji": "🚪",
    },
    "менеджеры": {
        "channel": "401",
        "label": "Менеджеры",
        "emoji": "👔",
    },
    "серверная": {
        "channel": "501",
        "label": "Серверная",
        "emoji": "🖥️",
    },
    "переговорка": {
        "channel": "601",
        "label": "Переговорка",
        "emoji": "🤝",
    },
}


def build_rtsp_url(channel: str) -> str:
    """Собрать RTSP-URL для заданного канала."""
    return f"{config.RTSP_BASE}/{channel}"


import subprocess


def capture_rtsp_frame(rtsp_url: str, timeout: int = 10) -> bytes:
    """
    Захватить один кадр с RTSP-потока через ffmpeg.
    """
    ffmpeg_path = '/usr/bin/ffmpeg'  # или shutil.which("ffmpeg")

    cmd = [
        ffmpeg_path,
        "-y",
        "-timeout", str(timeout * 1000000),
        "-rtsp_transport", "tcp",
        "-i", rtsp_url,
        "-vframes", "1",
        "-q:v", "3",
        "-f", "image2",
        "-loglevel", "error",
        "-",
    ]

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False
        )

        if result.returncode != 0 or not result.stdout:
            error_msg = result.stderr.decode("utf-8", errors="replace").strip()
            raise Exception(f"ffmpeg ошибка: {error_msg}")

        return result.stdout

    except subprocess.TimeoutExpired:
        raise Exception(f"Таймаут захвата кадра ({timeout} сек)")
    except FileNotFoundError:
        raise Exception(f"ffmpeg не найден по пути: {ffmpeg_path}")


# ---------------------------------------------------------------------------
# CameraBot
# ---------------------------------------------------------------------------

class CameraBot(Bot):
    """Бот для захвата и отправки скриншотов с IP-камер."""

    def __init__(self, nc_url: str):
        self.bot_name = BOT_NAME_CAMERA
        super().__init__(self.bot_name, nc_url)

        self.command_handlers = {
            "помощь": {
                self.HANDLER_FIELD: self.handle_help,
                self.HELP_TEXT_FIELD: "Справка по командам",
                self.ACCESS_FIELD: config.ADMINS
            },
            "разработчики": {
                self.HANDLER_FIELD: lambda *a: self.handle_camera("разработчики", *a),
                self.HELP_TEXT_FIELD: "💻 Показать разработчиков",
                self.ACCESS_FIELD: config.ADMINS
            },
            "коридор": {
                self.HANDLER_FIELD: lambda *a: self.handle_camera("коридор", *a),
                self.HELP_TEXT_FIELD: "🚪 Показать коридор",
                self.ACCESS_FIELD: config.ADMINS
            },
            "менеджеры": {
                self.HANDLER_FIELD: lambda *a: self.handle_camera("менеджеры", *a),
                self.HELP_TEXT_FIELD: "👔 Показать менеджеров",
                self.ACCESS_FIELD: config.ADMINS
            },
            "серверная": {
                self.HANDLER_FIELD: lambda *a: self.handle_camera("серверная", *a),
                self.HELP_TEXT_FIELD: "🖥️ Показать серверную",
                self.ACCESS_FIELD: config.ADMINS
            },
            "переговорка": {
                self.HANDLER_FIELD: lambda *a: self.handle_camera("переговорка", *a),
                self.HELP_TEXT_FIELD: "🤝 Показать переговорку",
                self.ACCESS_FIELD: config.ADMINS
            },
            "все_камеры": {
                self.HANDLER_FIELD: self.handle_all_cameras,
                self.HELP_TEXT_FIELD: "📷 Показать все камеры",
                self.ACCESS_FIELD: config.ADMINS
            },
        }

    async def handle_help(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Справка."""
        lines = [
            "📷 *CameraBot* — скриншоты с IP-камер\n\n"
            "Команды:\n"
        ]
        for cmd, obj in self.command_handlers.items():
            desc = obj.get(self.HELP_TEXT_FIELD, "")
            lines.append(f"• `{cmd}` — {desc}")
        lines.append("\nВсе команды отправляются через !")
        return "\n".join(lines)

    async def handle_camera(
        self, camera_key: str,
        command_args: list = None, user_id=None, room_token: str = None,
    ) -> str:
        """Захватить кадр с указанной камеры и отправить в чат."""
        camera = CAMERAS.get(camera_key)
        if not camera:
            return f"❌ Камера `{camera_key}` не найдена"

        label = camera["label"]
        emoji = camera["emoji"]
        rtsp_url = build_rtsp_url(camera["channel"])

        try:
            jpeg_data = capture_rtsp_frame(rtsp_url)
        except Exception as e:
            return f"❌ Ошибка захвата ({emoji} {label}): {e}"

        # Отправить изображение в чат
        try:
            await self._send_image_to_chat(room_token, jpeg_data, f"{emoji} {label} — {datetime.now().strftime('%H:%M:%S')}")
            return f"✅ {emoji} {label} — скриншот отправлен"
        except Exception as e:
            return f"❌ Ошибка отправки изображения: {e}"

    async def handle_all_cameras(self, command_args: list = None, user_id=None, room_token: str = None) -> str:
        """Захватить кадры со всех камер и отправить в чат."""
        results = []
        for key, camera in CAMERAS.items():
            label = camera["label"]
            emoji = camera["emoji"]
            rtsp_url = build_rtsp_url(camera["channel"])

            try:
                jpeg_data = capture_rtsp_frame(rtsp_url)
                try:
                    await self._send_image_to_chat(room_token, jpeg_data, f"{emoji} {label} — {datetime.now().strftime('%H:%M:%S')}")
                    results.append(f"✅ {emoji} {label}")
                except Exception as e:
                    results.append(f"❌ {emoji} {label} (отправка: {e})")
            except Exception as e:
                results.append(f"❌ {emoji} {label} ({e})")

        return "📷 *Все камеры*\n" + "\n".join(results)

    async def _send_image_to_chat(self, room_token: str, jpeg_data: bytes, caption: str = "") -> None:
        """
        Отправить JPEG-изображение в чат Nextcloud Talk.

        Использует files_sharing API: сначала загружает изображение на Nextcloud
        через WebDAV, затем шарит в чат.
        """
        import secrets

        from nextcloud.nextcloudapi import NextcloudClient

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"camera_{timestamp}.jpg"
        upload_dir = "tmp/camera_screenshots"

        nc_client = NextcloudClient(
            config.NEXTCLOUD_URL,
            config.NEXTCLOUD_API_USER,
            config.NEXTCLOUD_API_PASSWORD,
        )

        # Создать директорию, если нет
        try:
            nc_client.create_directory_recursive(upload_dir)
        except Exception:
            pass  # может уже существовать

        # Загрузить JPEG на Nextcloud через WebDAV
        remote_path = f"{upload_dir}/{filename}"
        url = f"{nc_client.webdav_base_url}/{remote_path}"

        import requests
        from requests.auth import HTTPBasicAuth

        resp = requests.put(url, data=jpeg_data, auth=HTTPBasicAuth(
            config.NEXTCLOUD_API_USER, config.NEXTCLOUD_API_PASSWORD
        ))
        if resp.status_code not in (201, 204):
            raise Exception(f"WebDAV PUT failed: {resp.status_code}")

        # Шарить файл в чат
        reference_id = secrets.token_hex(32)
        nc_client.share_file_to_chat(
            room_token=room_token,
            file_path=remote_path,
            caption=caption,
            reference_id=reference_id,
            silent=False,
        )
