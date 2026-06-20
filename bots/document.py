"""
DocumentBot — мониторит папку на Nextcloud, обрабатывает файлы через llama.cpp server,
отправляет результат в заданный чат Nextcloud Talk.

Поддерживаемые типы:
  • PDF (в т.ч. многостраничные) — извлечение текста с каждой страницы
  • Изображения (PNG, JPG, BMP, GIF, TIFF, WEBP) — OCR / описание
  • Текстовые документы (TXT, CSV, JSON, XML, HTML) — извлечение текста
"""

import asyncio
import base64
import json
import mimetypes
import os
import urllib.parse
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

import config
import httpx

from bots.common import Bot
from nextcloud.nextcloudapi import NextcloudClient

BOT_NAME_DOCUMENT = "bot_document"


# ---------------------------------------------------------------------------
# JSON-схемы для форматов вывода по типу документа
# ---------------------------------------------------------------------------

PDF_SCHEMA = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": ["pdf"]},
        "fileName": {"type": "string"},
        "pageCount": {"type": "integer"},
        "pages": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "page": {"type": "integer"},
                    "text": {"type": "string"},
                    "hasText": {"type": "boolean"}
                },
                "required": ["page", "text", "hasText"]
            }
        }
    },
    "required": ["docType", "fileName", "pageCount", "pages"]
}

IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": ["image"]},
        "fileName": {"type": "string"},
        "format": {"type": "string"},
        "width": {"type": "integer"},
        "height": {"type": "integer"},
        "hasText": {"type": "boolean"},
        "extractedText": {"type": "string"},
        "description": {"type": "string"}
    },
    "required": ["docType", "fileName", "hasText", "description"]
}

TEXT_SCHEMA = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": ["text"]},
        "fileName": {"type": "string"},
        "encoding": {"type": "string"},
        "lineCount": {"type": "integer"},
        "textContent": {"type": "string"}
    },
    "required": ["docType", "fileName", "textContent"]
}


# ---------------------------------------------------------------------------
# Маппинг MIME → тип обработки
# ---------------------------------------------------------------------------

PDF_MIMETYPES = {"application/pdf"}
IMAGE_MIMETYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/bmp",
    "image/gif", "image/tiff", "image/webp",
}
TEXT_MIMETYPES = {
    "text/plain", "text/csv", "application/json",
    "text/xml", "application/xml",
    "text/html", "application/xhtml+xml",
}

# Расшрения → MIME (для файлов без точного MIME)
EXT_MIME_MAP = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
    ".xml": "application/xml",
    ".html": "text/html",
    ".htm": "text/html",
}


def _detect_mime(file_path: str) -> str:
    """Определить MIME-тип файла по расширению."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in EXT_MIME_MAP:
        return EXT_MIME_MAP[ext]
    guess, _ = mimetypes.guess_type(file_path)
    return guess or "application/octet-stream"


def _detect_doc_type(file_path: str) -> str:
    """Вернуть 'pdf', 'image', 'text' или 'unknown'."""
    mime = _detect_mime(file_path)
    if mime in PDF_MIMETYPES:
        return "pdf"
    if mime in IMAGE_MIMETYPES:
        return "image"
    if mime in TEXT_MIMETYPES:
        return "text"
    return "unknown"


# ---------------------------------------------------------------------------
# Промт для llama.cpp
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
Ты — специализированный помощник для анализа документов.
Твоя задача: определить тип полученного документа, извлечь текстовые данные и вернуть результат в формате, соответствующем JSON-схеме для данного типа.

## PDF-документ (в т.ч. многостраничный)
Обработать каждую страницу отдельно. Если страница содержит текст — извлечь его. Если страница является изображением — дать словесное описание.
Ответ по схеме:
{pdf_schema}

## Изображение
Если на изображении есть текст — извлечь его (OCR). Если текста нет — дать подробное словесное описание изображения.
Ответ по схеме:
{image_schema}

## Текстовый документ
Извлечь всё текстовое содержимое.
Ответ по схеме:
{text_schema}

Важно:
- Возвращай ТОЛЬКО валидный JSON, без markdown-обёртки ```json``` и без пояснений.
- Если файл многостраничный PDF, обработай каждую страницу.
- Если изображение без текста — сделай словесное описание.
""".format(
    pdf_schema=json.dumps(PDF_SCHEMA, indent=2),
    image_schema=json.dumps(IMAGE_SCHEMA, indent=2),
    text_schema=json.dumps(TEXT_SCHEMA, indent=2),
)


# ---------------------------------------------------------------------------
# Llama.cpp HTTP клиент
# ---------------------------------------------------------------------------

class LlamaClient:
    """Клиент для llama.cpp server (OpenAI-compatible API)."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self.system_prompt = SYSTEM_PROMPT

    async def _completion(self, client: httpx.AsyncClient, messages: list) -> str:
        """Вызвать /v1/chat/completions на llama.cpp server."""
        url = f"{self.server_url}/v1/chat/completions"
        payload = {
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        resp = await client.post(url, json=payload, timeout=120.0)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def process_document(self, file_name: str, file_data: bytes,
                               doc_type: str) -> dict:
        """Отправить документ в llama.cpp и получить структурированный результат."""

        async with httpx.AsyncClient() as client:
            if doc_type == "image":
                # Для изображений отправляем base64 в рамках message content
                b64 = base64.b64encode(file_data).decode("ascii")
                user_msg = {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/png;base64,{b64}"}
                        },
                        {
                            "type": "text",
                            "text": (
                                f"Файл: {file_name}\n"
                                "Проанализируй изображение. Если на нём есть текст — извлеки его. "
                                "Если текста нет — дай подробное словесное описание. "
                                "Верни результат строго в формате JSON согласно схеме для типа 'image'."
                            )
                        }
                    ]
                }
            elif doc_type == "text":
                try:
                    text_content = file_data.decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text_content = file_data.decode("cp1251")
                    except UnicodeDecodeError:
                        text_content = file_data.decode("latin-1")

                # Обрезаем очень большие тексты
                if len(text_content) > 40000:
                    text_content = text_content[:40000] + "\n... [текст обрезан]"

                user_msg = {
                    "role": "user",
                    "content": (
                        f"Файл: {file_name}\nСодержимое:\n---\n{text_content}\n---\n"
                        "Проанализируй документ и верни результат строго в формате JSON "
                        "согласно схеме для типа 'text'."
                    )
                }
            else:
                # PDF — отправляем как base64
                b64 = base64.b64encode(file_data).decode("ascii")
                user_msg = {
                    "role": "user",
                    "content": (
                        f"Файл: {file_name} (PDF, {len(file_data)} байт)\n"
                        "PDF-файл передан в base64. "
                        "Определи количество страниц, извлеки текст с каждой страницы. "
                        "Если страница является изображением — опиши её словесно. "
                        "Верни результат строго в формате JSON согласно схеме для типа 'pdf'."
                        f"\n\nBase64:\n{b64}"
                    )
                }

            messages = [
                {"role": "system", "content": self.system_prompt},
                user_msg,
            ]

            result_text = await self._completion(client, messages)

            # Попытка распарсить JSON из ответа
            result_text = result_text.strip()
            # Убрать markdown-обёртку если есть
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                result_text = "\n".join(lines).strip()

            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                return {
                    "docType": doc_type,
                    "fileName": file_name,
                    "rawResponse": result_text,
                    "parseError": "Не удалось распарсить JSON-ответ от модели"
                }


# ---------------------------------------------------------------------------
# DocumentBot
# ---------------------------------------------------------------------------

class DocumentBot(Bot):
    """
    Бот, который периодически опрашивает заданную папку на Nextcloud,
    обрабатывает новые файлы через llama.cpp server и отправляет
    результат в указанный чат.
    """

    def __init__(self, nc_url: str):
        self.bot_name = BOT_NAME_DOCUMENT
        super().__init__(self.bot_name, nc_url)

        self.watch_dir = config.DOCUMENT_WATCH_DIR.rstrip("/")
        self.chat_room = config.DOCUMENT_CHAT_ROOM
        self.poll_interval = config.POLL_INTERVAL_SEC

        self.nc_client = NextcloudClient(
            config.NEXTCLOUD_URL,
            config.NEXTCLOUD_API_USER,
            config.NEXTCLOUD_API_PASSWORD,
        )
        self.llama = LlamaClient(config.LLAMA_SERVER_URL)

        # Трекер обработанных файлов (href → timestamp)
        self._processed: Dict[str, float] = {}

        # Флаг запуска background-задачи
        self._task: Optional[asyncio.Task] = None

    def start(self) -> None:
        """Запустить фоновый опрос папки."""
        if self._task and not self._task.done():
            return  # Уже запущен
        self._task = asyncio.create_task(self._poll_loop())

    def stop(self) -> None:
        """Остановить фоновый опрос."""
        if self._task:
            self._task.cancel()

    async def _poll_loop(self) -> None:
        """Бесконечный цикл опроса."""
        while True:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[DocumentBot] Ошибка при опросе: {e}")

            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break

    async def _poll_once(self) -> None:
        """Одно опросное итерация — найти новые файлы и обработать."""
        try:
            dirs, files, dirs_info, files_info = self.nc_client.get_files_recursive(
                self.watch_dir + "/"
            )
        except Exception as e:
            print(f"[DocumentBot] Не удалось получить список файлов: {e}")
            await self._notify(f"❌ Ошибка опроса папки: {e}")
            return

        if not files:
            return

        now = datetime.now().timestamp()

        for file_info in files_info:
            href = file_info["href"]
            # Убрать базовый путь WebDAV, оставить относительный путь файла
            rel_path = href.replace(self.nc_client.webdav_base_path, "", 1)
            if rel_path.startswith("/"):
                rel_path = rel_path[1:]

            file_name = os.path.basename(urllib.parse.unquote(rel_path))

            # Проверка: не обработан ли уже этот файл
            if href in self._processed:
                continue

            # Проверка типа
            doc_type = _detect_doc_type(file_name)
            if doc_type == "unknown":
                print(f"[DocumentBot] Пропускаю неизвестный тип: {file_name}")
                continue

            print(f"[DocumentBot] Найден новый файл: {file_name} ({doc_type})")
            await self._process_and_notify(rel_path, file_name, doc_type)

            # Отметить как обработанный
            self._processed[href] = now

            # Очистка старых записей (> 24ч)
            self._cleanup_processed(now)

    def _cleanup_processed(self, now: float) -> None:
        """Удалить записи старше 24 часов из трекера."""
        cutoff = now - 86400
        self._processed = {
            k: v for k, v in self._processed.items() if v > cutoff
        }

    async def _process_and_notify(self, rel_path: str, file_name: str,
                                  doc_type: str) -> None:
        """Скачать файл, обработать через llama.cpp, отправить результат в чат."""
        try:
            # Скачать содержимое файла
            file_data = self.nc_client.download_file_content(
                rel_path, encode_path=True
            )
        except Exception as e:
            msg = f"❌ Ошибка скачивания {file_name}: {e}"
            print(f"[DocumentBot] {msg}")
            await self._notify(msg)
            return

        try:
            result = await self.llama.process_document(file_name, file_data, doc_type)
        except Exception as e:
            msg = f"❌ Ошибка обработки {file_name} через llama.cpp: {e}"
            print(f"[DocumentBot] {msg}")
            await self._notify(msg)
            return

        # Сформировать сообщение для чата
        chat_msg = self._format_result(file_name, doc_type, result)
        await self._notify(chat_msg)

    @staticmethod
    def _format_result(file_name: str, doc_type: str, result: dict) -> str:
        """Сформировать читаемое сообщение из результата обработки."""

        if "parseError" in result:
            return (
                f"📄 *{file_name}*\n"
                f"⚠️ Обработка завершена с предупреждением:\n"
                f"{result.get('parseError', 'unknown error')}\n\n"
                f"Сырой ответ:\n{result.get('rawResponse', 'N/A')[:500]}"
            )

        if doc_type == "pdf":
            pages = result.get("pages", [])
            page_lines = []
            for p in pages:
                page_num = p.get("page", "?")
                text = p.get("text", "").strip()
                has_text = p.get("hasText", False)
                if has_text and text:
                    # Обрезать длинный текст для чата
                    preview = text[:300] + "..." if len(text) > 300 else text
                    page_lines.append(f"  **Страница {page_num}**: {preview}")
                else:
                    page_lines.append(f"  **Страница {page_num}**: (изображение, текст отсутствует)")

            return (
                f"📄 *{file_name}* (PDF, {result.get('pageCount', len(pages))} стр.)\n\n"
                + "\n".join(page_lines)
            )

        elif doc_type == "image":
            has_text = result.get("hasText", False)
            desc = result.get("description", "")
            extracted = result.get("extractedText", "")

            if has_text and extracted:
                preview = extracted[:500] + "..." if len(extracted) > 500 else extracted
                return (
                    f"🖼 *{file_name}* (изображение с текстом)\n\n"
                    f"**Извлечённый текст:**\n{preview}\n\n"
                    f"**Описание:** {desc}"
                )
            else:
                return (
                    f"🖼 *{file_name}* (изображение)\n\n"
                    f"**Описание:** {desc}"
                )

        elif doc_type == "text":
            content = result.get("textContent", "")
            lines = result.get("lineCount", 0)
            preview = content[:500] + "..." if len(content) > 500 else content
            return (
                f"📝 *{file_name}* (текст, {lines} стр.)\n\n"
                f"{preview}"
            )

        else:
            return (
                f"📎 *{file_name}*\n\n"
                f"Результат:\n{json.dumps(result, ensure_ascii=False, indent=2)[:1000]}"
            )

    async def _notify(self, message: str) -> None:
        """Отправить сообщение в заданный чат."""
        if not self.chat_room:
            print(f"[DocumentBot] (нет chat_room) {message}")
            return

        try:
            await self.send_to_nextcloud(self.chat_room, message, silent=True)
        except Exception as e:
            print(f"[DocumentBot] Ошибка отправки в чат: {e}")

    # -----------------------------------------------------------------------
    # Команды бота (для управления через чат)
    # -----------------------------------------------------------------------

    async def handle_help(self, args: list, user_id: str = None,
                          room_token: str = None) -> str:
        return (
            "🤖 *DocumentBot* — автоматическая обработка документов\n\n"
            "• Мониторит папку на Nextcloud на новые файлы\n"
            "• Обрабатывает PDF, изображения, текстовые документы\n"
            "• Отправляет результат в заданный чат\n\n"
            "Команды:\n"
            "• `статус` — информация о мониторинге\n"
            "• `пауза` — приостановить мониторинг\n"
            "• `старт` — возобновить мониторинг\n"
        )

    async def handle_status(self, args: list, user_id: str = None,
                            room_token: str = None) -> str:
        return (
            f"📊 *Статус DocumentBot*\n\n"
            f"• Папка: `{self.watch_dir}`\n"
            f"• Чат для результатов: `{self.chat_room}`\n"
            f"• Интервал опроса: {self.poll_interval}с\n"
            f"• Llama server: `{config.LLAMA_SERVER_URL}`\n"
            f"• Обработано файлов (сессия): {len(self._processed)}\n"
            f"• Активен: {'✅' if self._task and not self._task.done() else '❌'}\n"
        )

    async def handle_pause(self, args: list, user_id: str = None,
                           room_token: str = None) -> str:
        self.stop()
        return "⏸ Мониторинг приостановлен"

    async def handle_start(self, args: list, user_id: str = None,
                           room_token: str = None) -> str:
        self.start()
        return "▶️ Мониторинг возобновлён"

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    # Переопределяем регистрацию команд
    def _register_commands(self):
        self.command_handlers = {
            "помощь": {
                self.HANDLER_FIELD: self.handle_help,
                self.HELP_TEXT_FIELD: "Справка",
            },
            "статус": {
                self.HANDLER_FIELD: self.handle_status,
                self.HELP_TEXT_FIELD: "Статус мониторинга",
            },
            "пауза": {
                self.HANDLER_FIELD: self.handle_pause,
                self.HELP_TEXT_FIELD: "Приостановить мониторинг",
                self.ACCESS_FIELD: config.ADMINS,
            },
            "старт": {
                self.HANDLER_FIELD: self.handle_start,
                self.HELP_TEXT_FIELD: "Возобновить мониторинг",
                self.ACCESS_FIELD: config.ADMINS,
            },
        }
