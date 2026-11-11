"""
DocumentBot — мониторит папку на Nextcloud, обрабатывает файлы через llama.cpp server,
отправляет результат в заданный чат Nextcloud Talk.

Поддерживаемые типы источников:
  • PDF (в т.ч. многостраничные) — извлечение страниц → изображения
  • Изображения (PNG, JPG, BMP, GIF, TIFF, WEBP) — OCR / описание

Определяемые типы документов:
  • УПД (универсальный счёт-фактура)
  • Счёт-фактура
  • Счёт на оплату
  • Договор
  • Акт выполненных работ
  • Информационное письмо
  • Личные документы (паспорт, полис ОМС и т.п.)
  • Неопределённый документ (fallback)
"""

import asyncio
import base64
import json
import mimetypes
import os
import urllib.parse
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

import config
import httpx
from collections import Counter

from bots.common import Bot
from nextcloud.nextcloudapi import NextcloudClient
from repo.postgres import DocumentRepository
from utilites import pdf_to_jpg_base64, image_to_jpg_base64, pdf_to_jpg_base64_iter, _encode_jpeg_base64
from PIL import Image
from io import BytesIO

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

BOT_NAME_DOCUMENT = "bot_document"

# ---------------------------------------------------------------------------
# Бизнес-типы документов
# ---------------------------------------------------------------------------

DOCUMENT_TYPE_UPD = "upd"
DOCUMENT_TYPE_INVOICE = "invoice"
DOCUMENT_TYPE_PAYMENT_ORDER = "payment_order"
DOCUMENT_TYPE_CONTRACT = "contract"
DOCUMENT_TYPE_ACT = "act"
DOCUMENT_TYPE_LETTER = "letter"
DOCUMENT_TYPE_PERSONAL = "personal"
DOCUMENT_TYPE_UNKNOWN = "unknown"

DOCUMENT_TYPE_LABELS = {
    DOCUMENT_TYPE_UPD: "УПД",
    DOCUMENT_TYPE_INVOICE: "Счёт-фактура",
    DOCUMENT_TYPE_PAYMENT_ORDER: "Счёт на оплату",
    DOCUMENT_TYPE_CONTRACT: "Договор",
    DOCUMENT_TYPE_ACT: "Акт выполненных работ",
    DOCUMENT_TYPE_LETTER: "Информационное письмо",
    DOCUMENT_TYPE_PERSONAL: "Личный документ",
    DOCUMENT_TYPE_UNKNOWN: "Документ (неопределён)",
}

# ---------------------------------------------------------------------------
# JSON-схемы для каждого бизнес-типа документа
# ---------------------------------------------------------------------------

SCHEMA_UPD = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_UPD]},
        "fileName": {"type": "string"},
        "docNumber": {"type": "string", "description": "Номер УПД"},
        "docDate": {"type": "string", "description": "Дата документа (DD.MM.YYYY)"},
        "supplier": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Наименование поставщика"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"}
            }
        },
        "buyer": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Наименование покупателя"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"}
            }
        },
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "price": {"type": "number"},
                    "amount": {"type": "number"},
                    "vatRate": {"type": "number"},
                    "vatAmount": {"type": "number"}
                }
            }
        },
        "totalWithoutVat": {"type": "number"},
        "totalVat": {"type": "number"},
        "totalWithVat": {"type": "number"},
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_INVOICE = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_INVOICE]},
        "fileName": {"type": "string"},
        "docNumber": {"type": "string", "description": "Номер счёта-фактуры"},
        "docDate": {"type": "string", "description": "Дата документа (DD.MM.YYYY)"},
        "supplier": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"},
                "address": {"type": "string"}
            }
        },
        "buyer": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"},
                "address": {"type": "string"}
            }
        },
        "ground": {"type": "string", "description": "Основание (договор, акт и т.п.)"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "price": {"type": "number"},
                    "amount": {"type": "number"},
                    "vatRate": {"type": "number"},
                    "vatAmount": {"type": "number"}
                }
            }
        },
        "totalWithoutVat": {"type": "number"},
        "totalVat": {"type": "number"},
        "totalWithVat": {"type": "number"},
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_PAYMENT_ORDER = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_PAYMENT_ORDER]},
        "fileName": {"type": "string"},
        "docNumber": {"type": "string", "description": "Номер счёта"},
        "docDate": {"type": "string", "description": "Дата документа (DD.MM.YYYY)"},
        "seller": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"},
                "bank": {"type": "string"},
                "bik": {"type": "string"},
                "accountNumber": {"type": "string"},
                "correspondentAccount": {"type": "string"}
            }
        },
        "payer": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"}
            }
        },
        "paymentPurpose": {"type": "string", "description": "Наименование товара/услуги"},
        "amount": {"type": "number", "description": "Сумма к оплате"},
        "paymentDeadline": {"type": "string", "description": "Срок оплаты (DD.MM.YYYY)"},
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_CONTRACT = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_CONTRACT]},
        "fileName": {"type": "string"},
        "docNumber": {"type": "string", "description": "Номер договора"},
        "docDate": {"type": "string", "description": "Дата договора (DD.MM.YYYY)"},
        "place": {"type": "string", "description": "Место заключения"},
        "party1": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Полное наименование"},
                "shortName": {"type": "string", "description": "Сокращённое наименование"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"},
                "ogrn": {"type": "string"},
                "representative": {"type": "string", "description": "Представитель и основание"},
                "role": {"type": "string", "enum": ["заказчик", "исполнитель", "покупатель", "поставщик"]}
            }
        },
        "party2": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "shortName": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"},
                "ogrn": {"type": "string"},
                "representative": {"type": "string"},
                "role": {"type": "string", "enum": ["заказчик", "исполнитель", "покупатель", "поставщик"]}
            }
        },
        "subject": {"type": "string", "description": "Предмет договора"},
        "contractAmount": {"type": "number", "description": "Сумма договора"},
        "termStart": {"type": "string", "description": "Дата начала действия"},
        "termEnd": {"type": "string", "description": "Дата окончания действия"},
        "paymentTerms": {"type": "string", "description": "Порядок расчётов"},
        "keyObligations": {"type": "string", "description": "Ключевые обязательства"},
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_ACT = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_ACT]},
        "fileName": {"type": "string"},
        "docNumber": {"type": "string", "description": "Номер акта"},
        "docDate": {"type": "string", "description": "Дата акта (DD.MM.YYYY)"},
        "contractRef": {"type": "string", "description": "Ссылка на договор (номер, дата)"},
        "executor": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"}
            }
        },
        "customer": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "inn": {"type": "string"},
                "kpp": {"type": "string"}
            }
        },
        "periodFrom": {"type": "string", "description": "Период выполнения работ (с)"},
        "periodTo": {"type": "string", "description": "Период выполнения работ (по)"},
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "Наименование работ/услуг"},
                    "quantity": {"type": "number"},
                    "unit": {"type": "string"},
                    "price": {"type": "number"},
                    "amount": {"type": "number"}
                }
            }
        },
        "totalAmount": {"type": "number"},
        "hasDisputes": {"type": "boolean", "description": "Есть ли претензии"},
        "disputeText": {"type": "string"},
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_LETTER = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_LETTER]},
        "fileName": {"type": "string"},
        "docNumber": {"type": "string", "description": "Исходящий/входящий номер"},
        "docDate": {"type": "string", "description": "Дата письма (DD.MM.YYYY)"},
        "sender": {
            "type": "object",
            "properties": {
                "organization": {"type": "string"},
                "person": {"type": "string"},
                "position": {"type": "string"}
            }
        },
        "recipient": {
            "type": "object",
            "properties": {
                "organization": {"type": "string"},
                "person": {"type": "string"},
                "position": {"type": "string"}
            }
        },
        "subject": {"type": "string", "description": "Тема письма"},
        "summary": {"type": "string", "description": "Краткое содержание"},
        "fullText": {"type": "string", "description": "Полный текст письма"},
        "requiresAction": {"type": "boolean", "description": "Требует ли действий"},
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_PERSONAL = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_PERSONAL]},
        "fileName": {"type": "string"},
        "subType": {
            "type": "string",
            "enum": ["passport", "oms_policy", "snils", "driver_license", "certificate", "other"],
            "description": "Подтип: паспорт, полис ОМС, СНИЛС, водительские права, сертификат и т.д."
        },
        "holderName": {"type": "string", "description": "ФИО владельца"},
        "docNumber": {"type": "string", "description": "Номер документа"},
        "series": {"type": "string", "description": "Серия (для паспорта)"},
        "issuedBy": {"type": "string", "description": "Кем выдан"},
        "issueDate": {"type": "string", "description": "Дата выдачи"},
        "expiryDate": {"type": "string", "description": "Срок действия"},
        "personalData": {
            "type": "object",
            "properties": {
                "fullName": {"type": "string"},
                "dateOfBirth": {"type": "string"},
                "placeOfBirth": {"type": "string"},
                "address": {"type": "string"},
                "gender": {"type": "string"}
            }
        },
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

SCHEMA_UNKNOWN = {
    "type": "object",
    "properties": {
        "docType": {"type": "string", "enum": [DOCUMENT_TYPE_UNKNOWN]},
        "fileName": {"type": "string"},
        "detectedType": {"type": "string", "description": "Как модель предполагает тип документа"},
        "summary": {"type": "string", "description": "Краткое содержание"},
        "extractedText": {"type": "string", "description": "Извлечённый текст"},
        "keyFields": {
            "type": "object",
            "description": "Ключевые поля, обнаруженные в документе",
            "additionalProperties": True
        },
        "notes": {"type": "string"}
    },
    "required": ["docType", "fileName"]
}

# Карта: бизнес-тип → схема
SCHEMA_MAP = {
    DOCUMENT_TYPE_UPD: SCHEMA_UPD,
    DOCUMENT_TYPE_INVOICE: SCHEMA_INVOICE,
    DOCUMENT_TYPE_PAYMENT_ORDER: SCHEMA_PAYMENT_ORDER,
    DOCUMENT_TYPE_CONTRACT: SCHEMA_CONTRACT,
    DOCUMENT_TYPE_ACT: SCHEMA_ACT,
    DOCUMENT_TYPE_LETTER: SCHEMA_LETTER,
    DOCUMENT_TYPE_PERSONAL: SCHEMA_PERSONAL,
    DOCUMENT_TYPE_UNKNOWN: SCHEMA_UNKNOWN,
}

# ---------------------------------------------------------------------------
# Маппинг MIME → тип источника данных
# ---------------------------------------------------------------------------

PDF_MIMETYPES = {"application/pdf"}
IMAGE_MIMETYPES = {
    "image/png", "image/jpeg", "image/jpg", "image/bmp",
    "image/gif", "image/tiff", "image/webp",
}

# Расширения → MIME (для файлов без точного MIME)
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
}


def _detect_mime(file_path: str) -> str:
    """Определить MIME-тип файла по расширению."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in EXT_MIME_MAP:
        return EXT_MIME_MAP[ext]
    guess, _ = mimetypes.guess_type(file_path)
    return guess or "application/octet-stream"


def _detect_source_type(file_path: str) -> str:
    """Вернуть 'pdf' или 'image' — тип источника данных."""
    mime = _detect_mime(file_path)
    if mime in PDF_MIMETYPES:
        return "pdf"
    if mime in IMAGE_MIMETYPES:
        return "image"
    return "unknown"


# ---------------------------------------------------------------------------
# Промты для llama.cpp
# ---------------------------------------------------------------------------

_DOCUMENT_TYPES_DESCRIPTION = """
Типы документов для определения:

1. **УПД** (универсальный документ) — содержит слова "Универсальный передаточный документ", "УПД", объединяет функции счёта-фактуры и документа, подтверждающего отгрузку/реализацию.
2. **Счёт-фактура** — содержит "СЧЁТ-ФАКТУРА", разделы "Продавец", "Покупатель", ИНН, КПП, суммы НДС.
3. **Счёт на оплату** — содержит "СЧЁТ", реквизиты банковского счёта (БИК, расчётный счёт, корсчёт), сумма к оплате, наименование товара/услуги.
4. **Договор** — содержит "ДОГОВОР", "Стороны", "Предмет договора", подписи сторон, номера, даты.
5. **Акт выполненных работ** — содержит "АКТ", "Акт выполненных работ", "Акт оказания услуг", ссылки на договор, перечень работ/услуг с суммами.
6. **Информационное письмо** — входящее письмо, содержит исходящий/входящий номер, дату, обращение, информационный текст без финансовых реквизитов.
7. **Личный документ** — паспорт РФ, полис ОМС, СНИЛС, водительское удостоверение, сертификат: содержат персональные данные (ФИО, дата рождения, серия и номер).
""".strip()

_SYSTEM_PROMT_TEMPLATE = """\
Ты — специализированный помощник для анализа документов.
Твоя задача: определить тип полученного документа, извлечь структурированные данные и вернуть результат в формате JSON.

{types_description}

## Инструкция
1. Проанализируй содержимое документа (текст / изображение).
2. Определи, к какому из перечисленных типов относится документ.
3. Извлеки все доступные данные согласно JSON-схеме для определённого типа.
4. Если не можешь однозначно определить тип — используй тип "{unknown_type}" и опиши документ в полях `detectedType` и `summary`.

## JSON-схема для типа "{doc_type_label}"
{schema_json}

Важно:
- Данные в документе должны быть строго перенесены в json, не придумывай данные и не добавляй ничего от себя.
- Заполняй только те поля, которые реально есть в документе. Полей, которых нет в документе, НЕ заполняй — просто пропусти их (не ставь null и не выдумывай значения).
- Если найдены позиции с нулевой ценой или стоимостью - их исключать нельзя, такое может быть. Нужно собрать все позиции.
- Если найдены печать и подпись на документе - укажи это в поле `notes`.
- Даты — строго в формате DD.MM.YYYY (например, 05.11.2026).
- Числа (суммы, цены, количества) — без разделителей тысяч, десятичный разделитель — точка (например, 1250.5).
- Поле `fullText` (для писем) — дословный текст письма без сокращений и пересказа; поле `summary` — краткое содержание 2–4 предложениями.
- Отвечай на русском языке; собственные комментарии пиши по-русски, цитаты из документа сохраняй в оригинальном виде.
- Возвращай ТОЛЬКО валидный JSON, без markdown-обёртки ```json``` и без пояснений.
- Если документ многостраничный — объедини данные со всех страниц.
- Если страница пустая (чистый лист, случайный скан) — не добавляй данных от неё.
- Если страница перевёрнута (текст вверх ногами или боком) — извлекай данные в правильном направлении.
- Поле `docType` должно быть равно "{doc_type}".
""".strip()


def build_system_prompt(doc_type: str = DOCUMENT_TYPE_UNKNOWN) -> str:
    """Собрать system prompt с подставленной схемой для заданного типа."""
    label = DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)
    schema = SCHEMA_MAP.get(doc_type, SCHEMA_UNKNOWN)
    return _SYSTEM_PROMT_TEMPLATE.format(
        types_description=_DOCUMENT_TYPES_DESCRIPTION,
        unknown_type=DOCUMENT_TYPE_UNKNOWN,
        doc_type_label=label,
        schema_json=json.dumps(schema, indent=2, ensure_ascii=False),
        doc_type=doc_type,
    )


# Первый проход — определение типа документа
_CLASSIFY_PROMPT = """\
Определи тип приложенного документа.

{types_description}

Ответь ТОЛЬКО одним из значений docType: {type_values}

Без пояснений, без markdown, без кавычек и без JSON — только одно значение.
""".format(
    types_description=_DOCUMENT_TYPES_DESCRIPTION,
    type_values=", ".join(SCHEMA_MAP.keys()),
)


# ---------------------------------------------------------------------------
# Llama.cpp HTTP клиент
# ---------------------------------------------------------------------------

class LlamaClient:
    """Клиент для llama.cpp server (OpenAI-compatible API) + OCR-сервис."""

    def __init__(self, server_url: str, ocr_url: str = "", steps_count=3):
        self.server_url = server_url.rstrip("/")
        self.ocr_url = ocr_url.rstrip("/") if ocr_url else ""
        # Репозиторий документов (PostgreSQL) — для поиска контрагентов по ИНН/КПП.
        # Подключается через set_repo() после создания DocumentRepository.
        self._repo: Optional["DocumentRepository"] = None
        self._classify_prompt = _CLASSIFY_PROMPT
        if not isinstance(steps_count, int) or steps_count < 1 or steps_count % 2 == 0:
            raise Exception("параметр steps_count должен быть нечётным (1, 3, 5, ...)")
        self.steps_count = steps_count
        # Статистика по токенам текущего документа: {"prompt": N, "completion": M}
        # Сбрасывается в начале process_document.
        self._token_stats: Dict[str, int] = {"prompt": 0, "completion": 0}

    def _reset_token_stats(self) -> None:
        self._token_stats = {"prompt": 0, "completion": 0}

    def set_repo(self, repo: "DocumentRepository") -> None:
        """Привязать репозиторий БД для поиска контрагентов по ИНН/КПП."""
        self._repo = repo

    @staticmethod
    def _clean_inn_kpp(v: Any) -> Optional[str]:
        """ИНН/КПП — чистые цифры без пробелов и тире."""
        if v is None:
            return None
        s = str(v).strip()
        if not s:
            return None
        s = "".join(c for c in s if c.isdigit())
        return s or None

    async def find_counterparty(self, inn: Any, kpp: Any) -> Optional[dict]:
        """Найти контрагента в БД по ИНН и КПП.

        Логика поиска (как при сохранении — upsert_counterparty):
          • с ИНН и КПП — точное совпадение (inn = $1 AND kpp = $2);
          • с ИНН без КПП — поиск по (INN, KPP IS NULL);
          • без ИНН — None.
        Возвращает dict с реквизитами найденного контрагента или None.
        """
        if self._repo is None or self._repo._pool is None:
            return None

        inn_c = self._clean_inn_kpp(inn)
        kpp_c = self._clean_inn_kpp(kpp)
        if not inn_c:
            return None

        try:
            async with self._repo._pool.acquire() as conn:
                if kpp_c:
                    row = await conn.fetchrow(
                        "SELECT * FROM counterparties WHERE inn = $1 AND kpp = $2 LIMIT 1",
                        inn_c, kpp_c,
                    )
                else:
                    row = await conn.fetchrow(
                        "SELECT * FROM counterparties WHERE inn = $1 AND kpp IS NULL LIMIT 1",
                        inn_c,
                    )
        except Exception as e:
            logger.info(f"[LlamaClient] Ошибка поиска контрагента в БД: {e}")
            return None

        if not row:
            return None

        return {
            "name": row["name"],
            "shortName": row["short_name"],
            "inn": row["inn"],
            "kpp": row["kpp"],
            "ogrn": row["ogrn"],
            "address": row["address"],
            "bank": row["bank"],
            "bik": row["bik"],
            "accountNumber": row["account_number"],
            "correspondentAccount": row["correspondent_account"],
            "representative": row["representative"],
        }

    def _add_usage(self, usage: Optional[dict]) -> None:
        """Накопить usage из ответа модели в статистику токенов."""
        if not usage:
            return
        self._token_stats["prompt"] += int(usage.get("prompt_tokens") or 0)
        self._token_stats["completion"] += int(usage.get("completion_tokens") or 0)

    async def ocr_recognize(self, client: httpx.AsyncClient,
                            images_base64: List[str],
                            file_name: str) -> tuple:
        """
        Отправить страницы в OCR-сервис (POST /recognize, multipart).
        Возвращает (full_text, page_texts):
          full_text  — весь текст для классификации,
          page_texts — список текстов по страницам (для постраничного извлечения).
        """
        if not self.ocr_url:
            return "", []

        all_texts = []
        for i, b64 in enumerate(images_base64):
            try:
                img_bytes = base64.b64decode(b64)
                files = {"file": (f"page_{i + 1}.jpg", img_bytes, "image/jpeg")}
                data = {"engine": "tesseract"}

                resp = await client.post(
                    f"{self.ocr_url}/recognize",
                    files=files,
                    data=data,
                    timeout=120.0,
                )
                resp.raise_for_status()
                result = resp.json()
                text = result.get("full_text", "").strip()
                all_texts.append(text)
                logger.info(f"[OCR] Страница {i + 1}/{len(images_base64)}: {len(text)} символов")
            except Exception as e:
                logger.info(f"[OCR] Страница {i + 1} ошибка: {e}")
                all_texts.append("")

        full_text = "\n\n".join(t for t in all_texts if t)
        logger.info(f"[OCR] Итого: {len(full_text)} символов из {len(images_base64)} страниц")
        return full_text, all_texts

    async def _completion(self, client: httpx.AsyncClient, messages: list,
                          response_format: Optional[dict] = None) -> tuple:
        """Вызвать /v1/chat/completions на llama.cpp server.

        Возвращает (content, usage), где usage — dict из ответа модели
        (prompt_tokens / completion_tokens / total_tokens) или None.

        response_format — опционально, например {"type": "json_object"} для
        принудительного JSON-ответа; по умолчанию не задаётся.
        """
        url = f"{self.server_url}/v1/chat/completions"
        payload = {
            "messages": messages,
            "temperature": 0.0,
        }
        if response_format:
            payload["response_format"] = response_format

        resp = await client.post(url, json=payload, timeout=360.0)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage")
        self._add_usage(usage)
        return content, usage

    async def determine_orientation(self, client: httpx.AsyncClient,
                                    b64: str) -> int:
        """Спросить LLM, на какой угол нужно повернуть страницу (0/90/180/270).

        Возвращает угол в градусах (на который нужно повернуть изображение
        ПРОТИВ часовой стрелки, чтобы текст читался нормально).
        При ошибке — 0 (не поворачиваем).
        """
        messages = [
            {"role": "system", "content": (
                "Ты определяешь ориентацию скана страницы документа. "
                "Внимательно посмотри на изображение и определи, в каком направлении "
                "расположен текст. Выбери ОДИН вариант:\n"
                "A — текст читается нормально: строки горизонтальны, слева направо, сверху вниз (поворот 0°);\n"
                "B — текст повёрнут на 90° по часовой стрелке: чтобы прочитать, нужно повернуть изображение на 90° против часовой стрелки;\n"
                "C — текст перевёрнут на 180°: строки горизонтальны, но читаются снизу вверх (поворот 180°);\n"
                "D — текст повёрнут на 270° по часовой стрелке (90° против часовой): чтобы прочитать, нужно повернуть изображение на 270° против часовой стрелки.\n"
                "Отвечай ТОЛЬКО одной буквой: A, B, C или D. Без пояснений."
            )},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": "В каком направлении расположен текст на этом изображении? A, B, C или D?"}
            ]}
        ]
        letter_to_angle = {"A": 0, "B": 90, "C": 180, "D": 270}
        try:
            content, _ = await self._completion(client, messages)
            for ch in content.strip().upper():
                if ch in letter_to_angle:
                    return letter_to_angle[ch]
            # запасной вариант: модель всё-таки ответила числом
            for token in content.strip().replace(',', ' ').split():
                if token in ("0", "90", "180", "270"):
                    return int(token)
            logger.info(f"[orientation] Не удалось разобрать ответ: {content!r}")
        except Exception as e:
            logger.info(f"[orientation] Ошибка определения ориентации: {e}")
        return 0

    async def rotate_images_if_needed(self, client: httpx.AsyncClient,
                                      images_base64: List[str]) -> List[str]:
        """Для каждой страницы определить ориентацию через LLM и повернуть.

        Используется только когда OCR-сервис недоступен (image-based пайплайн).
        Запросы идут последовательно, чтобы не перегружать llama.cpp.
        """
        rotated = []
        for i, b64 in enumerate(images_base64):
            angle = await self.determine_orientation(client, b64)
            if angle == 0:
                rotated.append(b64)
                continue
            try:
                img = Image.open(BytesIO(base64.b64decode(b64)))
                img = img.rotate(-angle, expand=True)  # OSD-угол: поворот против ЧС
                rotated.append(_encode_jpeg_base64(img))
                logger.info(f"[orientation] Страница {i + 1}: повернута на {angle}°")
            except Exception as e:
                logger.info(f"[orientation] Страница {i + 1}: не удалось повернуть: {e}")
                rotated.append(b64)
        return rotated

    async def classify_document(self, client: httpx.AsyncClient,
                                images_base64: List[str],
                                file_name: str) -> str:
        """Определить тип документа (первый проход)."""
        content_parts = []
        for b64 in images_base64:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })
        content_parts.append({
            "type": "text",
            "text": f"Файл: {file_name}\n{self._classify_prompt}"
        })

        messages = [
            {"role": "system", "content": "Ты — классификатор документов. Отвечай только одним значением типа."},
            {"role": "user", "content": content_parts}
        ]

        content, _ = await self._completion(client, messages)
        result = content.strip().strip("`").strip()

        # Попробовать распарсить как JSON на случай, если модель вернула объект
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                detected = parsed.get("docType", parsed.get("type", ""))
                if detected in SCHEMA_MAP:
                    return detected
        except (json.JSONDecodeError, AttributeError):
            pass

        # Иначе — ищем значение типа в тексте
        for doc_type in SCHEMA_MAP:
            if doc_type in result.lower():
                return doc_type

        return DOCUMENT_TYPE_UNKNOWN

    async def classify_document_text(self, client: httpx.AsyncClient,
                                     ocr_text: str, file_name: str) -> str:
        """Классификация по OCR-тексту (без изображений)."""
        messages = [
            {"role": "system", "content": "Ты — классификатор документов. Отвечай только одним значением типа."},
            {"role": "user", "content": (
                f"Файл: {file_name}\n"
                f"Текст документа:\n{ocr_text[:4000]}\n\n"
                f"{self._classify_prompt}"
            )}
        ]

        content, _ = await self._completion(client, messages)
        result = content.strip().strip("`").strip()

        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                detected = parsed.get("docType", parsed.get("type", ""))
                if detected in SCHEMA_MAP:
                    return detected
        except (json.JSONDecodeError, AttributeError):
            pass

        for doc_type in SCHEMA_MAP:
            if doc_type in result.lower():
                return doc_type

        return DOCUMENT_TYPE_UNKNOWN

    async def extract_document_data(self, client: httpx.AsyncClient,
                                     images_base64: List[str],
                                     file_name: str,
                                     doc_type: str) -> dict:
        """Извлечь структурированные данные согласно схеме для типа.

        Стратегия для многостраничных документов:
          1. Каждая страница обрабатывается отдельно; модель видит текущую
             страницу + накопленный JSON с предыдущих страниц и возвращает
             ОБНОВЛЁННЫЙ полный JSON (накопительный).
          2. После обработки всех страниц — финальный merge-проход: модель
             объединяет N промежуточных JSON в один итоговый (страховка от
             потерь данных на границах страниц и от дрейфа накопительного
             контекста).
        """
        system_prompt = build_system_prompt(doc_type)
        label = DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)

        # Список промежуточных результатов (JSON-строки с каждой страницы).
        _results: List[str] = []

        for idx in range(len(images_base64)):
            b64 = images_base64[idx]
            page = idx + 1

            # Составная часть запроса.
            content_parts = [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
                },
            ]

            text_lines = [
                f"Страница {page} из {len(images_base64)} файла {file_name}",
                f"Документ классифицирован как: {label}",
            ]
            if _results:
                text_lines.append(
                    "С предыдущих страниц уже извлечён JSON — ВОССТАНОВИ его полностью и ДОПОЛНИ данными текущей страницы:\n"
                    + "\n".join(_results)
                )
            text_lines.append(
                f"Извлеки структурированные данные согласно JSON-схеме для типа \"{doc_type}\". "
                f"Верни ОБНОВЛЁННЫЙ полный JSON (данные со всех страниц, включая текущую).\n"
                f"СТРОГИЕ ПРАВИЛА:\n"
                f"- НИ ОДНА позиция (item) не может быть исключена или потеряна. "
                f"Позиции с нулевой ценой, нулевой стоимостью или нулевым количеством — ОБЯЗАТЕЛЬНО сохраняй.\n"
                f"- Не добавляй никаких данных, которых нет на страницах документа.\n"
                f"- Все позиции со всех страниц должны присутствовать в итоговом JSON без исключений.\n"
                f"- Если страница пустая (чистый лист, случайный скан) — не добавляй ничего, "
                f"просто верни накопленный JSON без изменений.\n"
                f"- Если страница перевёрнута (текст вверх ногами или боком) — извлекай данные "
                f"в правильном направлении, как будто страницу повернули.\n"
                f"- Возвращай ТОЛЬКО валидный JSON."
            )
            content_parts.append({"type": "text", "text": "\n".join(text_lines)})

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts}
            ]

            result_text, _ = await self._completion(
                client, messages, response_format={"type": "json_object"}
            )

            # Очистка markdown-обёртки (на случай, если модель всё равно обёрнула)
            result_text = self._strip_markdown(result_text)
            if result_text:
                _results.append(result_text)

        if not _results:
            return {
                "docType": doc_type,
                "fileName": file_name,
                "parseError": "Модель не вернула ни одного результата по страницам",
            }

        # Единственная страница — merge-проход не нужен.
        if len(_results) == 1:
            return self._parse_extract_result(_results[0], doc_type, file_name)

        # Финальный merge-проход: объединить N промежуточных JSON в один.
        merged = await self._merge_page_results(client, system_prompt, _results, doc_type, file_name)
        if merged is not None:
            return merged
        # Fallback: последний накопительный JSON (он содержит данные со всех страниц).
        return self._parse_extract_result(_results[-1], doc_type, file_name)

    @staticmethod
    def _strip_markdown(text: str) -> str:
        """Убрать markdown-обёртку ```json ... ``` из ответа модели."""
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        return text

    @staticmethod
    def _parse_extract_result(result_text: str, doc_type: str, file_name: str) -> dict:
        """Распарсить JSON-ответ модели; при ошибке — fallback-структура."""
        try:
            return json.loads(result_text)
        except json.JSONDecodeError:
            return {
                "docType": doc_type,
                "fileName": file_name,
                "rawResponse": result_text,
                "parseError": "Не удалось распарсить JSON-ответ от модели",
            }

    async def _merge_page_results(self, client: httpx.AsyncClient,
                                  system_prompt: str,
                                  page_results: List[str],
                                  doc_type: str, file_name: str) -> Optional[dict]:
        """Финальный проход: объединить N промежуточных JSON в один итоговый.

        Возвращает dict при успехе, None — если merge не удался (тогда
        вызывающий кодит fallback на последний накопительный JSON).
        """
        merged_text = "\n\n".join(page_results)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Файл: {file_name}\n"
                f"Ниже — промежуточные JSON-результаты извлечения данных по страницам "
                f"многостраничного документа (тип: \"{doc_type}\").\n"
                f"Объедини их в ОДИН итоговый JSON.\n"
                f"СТРОГИЕ ПРАВИЛА:\n"
                f"- НИ ОДНА позиция (item) не может быть исключена. "
                f"Позиции с нулевой ценой, нулевой стоимостью или нулевым количеством — ОБЯЗАТЕЛЬНО сохраняй.\n"
                f"Если попались одинаковые позиции, это не ошибка — ОБЯЗАТЕЛЬНО сохраняй все.\n"
                f"- Не добавляй никаких данных, которых нет в промежуточных JSON.\n"
                f"- Все позиции со всех страниц должны присутствовать в итоговом JSON без исключений.\n"
                f"- Для противоречивых скалярных полей выбери наиболее полное/корректное значение.\n"
                f"- Возвращай ТОЛЬКО валидный JSON.\n\n"
                f"{merged_text}"
            )}
        ]
        try:
            result_text, _ = await self._completion(
                client, messages, response_format={"type": "json_object"}
            )
        except Exception as e:
            logger.info(f"[LlamaClient] Merge-проход ошибка: {e}")
            return None
        result_text = self._strip_markdown(result_text)
        try:
            merged = json.loads(result_text)
            if isinstance(merged, dict):
                return merged
        except json.JSONDecodeError:
            pass
        logger.info("[LlamaClient] Merge-проход не дал валидный JSON — использую fallback")
        return None

    async def _ocr_page_llm_refine(self, client: httpx.AsyncClient,
                                    b64: str,
                                    ocr_page_text: str) -> Optional[str]:
        """LLM-распознавание одной страницы поверх OCR-текста.

        Используется для краевых страниц многостраничных документов (первая и
        последняя) и для одностраничных документов, когда основной пайплайн
        работает через OCR. Модель видит изображение + черновик от OCR и
        возвращает исправленный полный текст страницы.

        Возвращает исправленный текст или None при ошибке/пустом ответе.
        """
        messages = [
            {"role": "system", "content": (
                "Ты — помощник по распознаванию текста со сканов документов. "
                "Тебе дают изображение страницы и предварительный текст, полученный OCR (tesseract). "
                "OCR часто ошибается в цифрах, датах, ИНН/КПП, названиях организаций и т.п."
            )},
            {"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                {"type": "text", "text": (
                    f"Предварительный текст страницы (OCR):\n{ocr_page_text or '(пусто)'}\n\n"
                    f"Внимательно посмотри на изображение и исправь ошибки OCR. "
                    f"Верни ПОЛНЫЙ исправленный текст страницы дословно, как он написан в документе: "
                    f"сохрани структуру (строки, таблицы — по строкам), не сокращай и не пересказывай. "
                    f"Если страница пустая — ответь одной строкой \"ПУСТО\". "
                    f"Никаких пояснений, без markdown-обёртки."
                )}
            ]}
        ]
        try:
            content, _ = await self._completion(client, messages)
            text = self._strip_markdown(content).strip()
            if not text or text.upper() == "ПУСТО":
                return None
            return text
        except Exception as e:
            logger.info(f"[OCR+LLM] Ошибка LLM-распознавания страницы: {e}")
            return None

    async def refine_pages_with_llm(self, client: httpx.AsyncClient,
                                    images_base64: List[str],
                                    page_texts: List[str]) -> tuple:
        """Улучшить OCR-тексты краевых страниц через LLM (image-based проход).

        Стратегия:
          • Одностраничный документ — LLM распознаёт единственную страницу.
          • Многостраничный — LLM распознаёт только первую и последнюю страницы
            (на них обычно шапка с реквизитами, итоги, подписи — самые важные
            для извлечения данные), остальные остаются по OCR.

        Запросы идут последовательно, чтобы не перегружать llama.cpp.
        Возвращает обновлённые (full_text, page_texts).
        """
        n = len(images_base64)
        if n == 0:
            return "", []

        refine_indices = [0] if n == 1 else {0, n - 1}
        for idx in sorted(refine_indices):
            b64 = images_base64[idx]
            ocr_text = page_texts[idx] if idx < len(page_texts) else ""
            logger.info(f"[OCR+LLM] Улучшаю страницу {idx + 1}/{n} через LLM")
            refined = await self._ocr_page_llm_refine(client, b64, ocr_text)
            if refined:
                page_texts[idx] = refined
                logger.info(f"[OCR+LLM] Страница {idx + 1}: исправлено ({len(refined)} символов)")
            else:
                logger.info(f"[OCR+LLM] Страница {idx + 1}: LLM не дала текста — оставляю OCR")

        full_text = "\n\n".join(t for t in page_texts if t)
        return full_text, page_texts

    async def extract_document_data_text(self, client: httpx.AsyncClient,
                                         ocr_text: str,
                                         file_name: str,
                                         doc_type: str) -> dict:
        """Извлечь данные по полному OCR-тексту (fallback, без постраничного разделения)."""
        system_prompt = build_system_prompt(doc_type)
        label = DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                f"Файл: {file_name}\n"
                f"Документ классифицирован как: {label}\n"
                f"Текст документа (OCR):\n{ocr_text}\n\n"
                f"Извлеки структурированные данные согласно JSON-схеме для типа \"{doc_type}\".\n"
                f"СТРОГИЕ ПРАВИЛА:\n"
                f"- Каждая позиция товара/услуги — ОТДЕЛЬНЫЙ элемент массива `items`.\n"
                f"- Позиции с нулевой ценой или стоимостью — ОБЯЗАТЕЛЬНО сохраняй.\n"
                f"- Возвращай ТОЛЬКО валидный JSON."
            )}
        ]

        result_text, _ = await self._completion(
            client, messages, response_format={"type": "json_object"}
        )

        result_text = self._strip_markdown(result_text)
        return self._parse_extract_result(result_text, doc_type, file_name)

    async def extract_document_data_text_per_page(self, client: httpx.AsyncClient,
                                                  page_texts: List[str],
                                                  file_name: str,
                                                  doc_type: str) -> dict:
        """
        Постраничное извлечение данных по OCR-тексту.

        Каждая страница обрабатывается отдельно; модель видит текущую страницу
        + накопленный JSON с предыдущих страниц и возвращает ОБНОВЛЁННЫЙ полный
        JSON (накопительный). После всех страниц — merge-проход (если страниц > 1).
        """
        system_prompt = build_system_prompt(doc_type)
        label = DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)

        _results: List[str] = []

        for idx in range(len(page_texts)):
            page_text = page_texts[idx]
            if not page_text:
                continue
            page = idx + 1

            content_parts = [
                {"type": "text", "text": (
                    f"Страница {page} из {len(page_texts)} файла {file_name}\n"
                    f"Документ классифицирован как: {label}\n"
                    f"Текст страницы (OCR):\n{page_text}\n\n"
                    + (
                        "С предыдущих страниц уже извлечён JSON — ВОССТАНОВИ его полностью и ДОПОЛНИ данными текущей страницы:\n"
                        + "\n".join(_results)
                        if _results else ""
                    )
                    + (
                        f"Извлеки структурированные данные согласно JSON-схеме для типа \"{doc_type}\". "
                        f"Верни ОБНОВЛЁННЫЙ полный JSON (данные со всех страниц, включая текущую).\n"
                        f"СТРОГИЕ ПРАВИЛА:\n"
                        f"- Каждая позиция товара/услуги на странице — ОТДЕЛЬНЫЙ элемент массива `items`.\n"
                        f"- Если позиция уже есть в накопленном JSON — добавляй её повторно - это не дубль, такое может быть.\n"
                        f"- Не объединяй разные позиции в одну. Позиции отличаются кодом курса (Б.x.x) или наименованием.\n"
                        f"- Позиции с нулевой ценой или стоимостью — ОБЯЗАТЕЛЬНО сохраняй.\n"
                        f"- Если страница не содержит позиций (шапка, итоги, подписи) — верни накопленный JSON без изменений.\n"
                        f"- Возвращай ТОЛЬКО валидный JSON."
                    )
                )}
            ]

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content_parts}
            ]

            result_text, _ = await self._completion(
                client, messages, response_format={"type": "json_object"}
            )
            result_text = self._strip_markdown(result_text)
            if result_text:
                _results.append(result_text)
                logger.info(f"[Extract] Страница {page}/{len(page_texts)}: OK ({len(result_text)} символов)")

        if not _results:
            return {
                "docType": doc_type,
                "fileName": file_name,
                "parseError": "Модель не вернула ни одного результата по страницам",
            }

        if len(_results) == 1:
            return self._parse_extract_result(_results[0], doc_type, file_name)

        merged = await self._merge_page_results(client, system_prompt, _results, doc_type, file_name)
        if merged is not None:
            return merged
        return self._parse_extract_result(_results[-1], doc_type, file_name)

    async def process_document(self, file_name: str, file_data: bytes,
                               source_type: str) -> dict:
        """
        Полный пайплайн: OCR → классификация → извлечение данных (несколько проходов с голосованием).
        source_type: 'pdf' или 'image' — формат источника.
        """
        # Конвертируем в список изображений base64 (фолбэк, если OCR не работает)
        if source_type == "pdf":
            images_base64 = list(pdf_to_jpg_base64_iter(file_data))
        else:
            rotated_b64 = image_to_jpg_base64(file_data)
            if rotated_b64:
                images_base64 = [rotated_b64]
            else:
                # Fallback: оригинальное изображение без поворота
                b64 = base64.b64encode(file_data).decode("ascii")
                images_base64 = [b64]

        if not images_base64 or len(images_base64) == 0:
            return {}

        # Сбрасываем статистику по токенам перед обработкой документа.
        self._reset_token_stats()

        async with httpx.AsyncClient() as client:
            # Шаг 0: OCR — получить текст из документа (отдельный llama.cpp с моделью распознавания)
            ocr_text, page_texts = await self.ocr_recognize(client, images_base64, file_name)
            use_text = bool(ocr_text)  # если OCR дал текст — отправляем текст, иначе изображения

            if use_text:
                # Улучшение качества: краевые страницы (первая/последняя для
                # многостраничных, единственная для одностраничных) дополнительно
                # распознаются через LLM по изображению поверх OCR-текста.
                ocr_text, page_texts = await self.refine_pages_with_llm(
                    client, images_base64, page_texts
                )
                use_text = bool(ocr_text)

            if use_text:
                logger.debug(f"[LlamaClient] Используем OCR-текст:\n{ocr_text}")
                logger.info(f"[LlamaClient] Используем OCR-текст ({len(ocr_text)} символов) вместо изображений")
            else:
                logger.info(f"[LlamaClient] OCR недоступен, используем изображения")
                # OCR-сервис не повернул страницы — определяем ориентацию через LLM
                images_base64 = await self.rotate_images_if_needed(client, images_base64)

            # Шаг 1: классификация (один раз)
            if use_text:
                doc_type = await self.classify_document_text(client, ocr_text, file_name)
            else:
                # Для определения типа документа достаточно отправить первую страницу.
                first_page = images_base64[0]
                doc_type = await self.classify_document(client, [first_page], file_name)
            logger.info(f"[LlamaClient] Определён тип: {doc_type} ({DOCUMENT_TYPE_LABELS.get(doc_type)})")

            # Шаг 2: извлечение данных — steps_count проходов (параллельно).
            # Запускаем все проходы одновременно через asyncio.gather в ТЕКУЩЕМ
            # event loop. Важно: НЕ используем блокирующие вызовы (ThreadPoolExecutor
            # + as_completed из main thread) — они блокируют event loop и
            # превращаются в deadlock (рабочие потоки ждут loop, а loop ждёт потоки),
            # из-за чего перестаёт отвечать веб-интерфейс.

            async def _run_extract(attempt: int) -> dict:
                """Один проход извлечения данных."""
                try:
                    if use_text:
                        # Одностраничный и многостраничный — полный текст сразу
                        r = await self.extract_document_data_text(client, ocr_text, file_name, doc_type)
                    else:
                        r = await self.extract_document_data(client, images_base64, file_name, doc_type)
                    logger.info(f"[LlamaClient] Проход {attempt + 1}/{self.steps_count}: {'OK' if 'parseError' not in r else 'ERROR'}")
                    return r
                except Exception as e:
                    logger.info(f"[LlamaClient] Проход {attempt + 1}/{self.steps_count} ошибка: {e}")
                    return {"docType": doc_type, "fileName": file_name, "parseError": str(e)}

            # Все проходы стартуют сразу и выполняются параллельно.
            raw_results = await asyncio.gather(*(_run_extract(attempt) for attempt in range(self.steps_count)))
            # явно отфильтровываем неудачные проходы — тогда голосование честное
            results = [r for r in raw_results if "parseError" not in r]
            # Шаг 3: голосование — берём значения с большинством
            result = self._vote_results(results, doc_type, file_name)

            # Шаг 4: контрагенты из БД. Если организация уже есть в БД (по ИНН/КПП),
            # подставляем её реквизиты вместо данных, распознанных из документа.
            await self._resolve_counterparties_from_db(result)

            # Для unknown-типа в режиме use_text подставляем OCR-текст в extractedText
            # кодом (модель не должна копировать весь текст в JSON — это тратит токены).
            if doc_type == DOCUMENT_TYPE_UNKNOWN and use_text:
                result["extractedText"] = ocr_text

            # Статистика по токенам (сколько отправлено / получено по всему документу).
            result["tokenStats"] = {
                "promptTokens": self._token_stats["prompt"],
                "completionTokens": self._token_stats["completion"],
            }
            return result

    async def _resolve_counterparties_from_db(self, result: dict) -> None:
        """Заменить данные контрагентов в результате на реквизиты из БД.

        Для каждого блока контрагента (supplier/buyer/seller/payer/executor/
        customer/party1/party2/sender/recipient), у которого есть ИНН, ищем
        организацию в БД по (ИНН, КПП). Если найдена — подставляем её
        реквизиты (name, shortName, inn, kpp, ogrn, address, bank, bik,
        accountNumber, correspondentAccount, representative) вместо распознанных.
        """
        if self._repo is None:
            return

        cp_keys = (
            "supplier", "buyer", "seller", "payer",
            "executor", "customer", "party1", "party2",
            "sender", "recipient",
        )
        for key in cp_keys:
            data = result.get(key)
            if not isinstance(data, dict):
                continue
            inn = data.get("inn")
            if not inn:
                continue
            found = await self.find_counterparty(inn, data.get("kpp"))
            if not found:
                continue
            for field, value in found.items():
                if value is not None:
                    data[field] = value
            logger.info(
                f"[LlamaClient] Контрагент {key} найден в БД "
                f"(ИНН {self._clean_inn_kpp(inn)}): подставлены реквизиты из БД"
            )

    @staticmethod
    def _normalize_for_vote(v: Any) -> Any:
        """Приводит значение к хешируемому виду для подсчёта голосов."""
        if v is None:
            return "__NONE__"
        try:
            hash(v)
            return v
        except TypeError:
            return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def _normalize_list(v: Any) -> str:
        """Каноническая строка для элемента списка (позиции)."""
        return json.dumps(v, sort_keys=True, ensure_ascii=False, default=str)

    @staticmethod
    def _vote_value(values: list) -> Any:
        """
        Голосование для скалярного значения.

        Возвращает значение, набравшее строгое большинство (>50%) среди
        непустых голосов. Если строгого большинства нет (в т.ч. ничья
        при чётном числе голосов или все значения разные) — возвращает None.

        Для списков (массивов, например items) используется НЕ точное
        сравнение всего массива, а слияние уникальных элементов: позиция
        сохраняется, если она встречается в >50% результатов. Иначе длинные
        накопительные списки с последних страниц многостраничных документов
        не могли набрать строгого большинства (модель слегка варьирует JSON)
        и позиции с последних страниц терялись.
        """
        non_none = [v for v in values if v is not None]
        if not non_none:
            return None

        # Массивы голосуем по элементам (см. выше).
        if any(isinstance(v, list) for v in non_none):
            return LlamaClient._vote_list(non_none)

        norms = [LlamaClient._normalize_for_vote(v) for v in non_none]
        counts = Counter(norms)

        # При равенстве частот most_common может вернуть любой из лидеров,
        # поэтому явно проверяем, что лидер единственный.
        best_norm, best_count = counts.most_common(1)[0]
        leaders = [n for n, c in counts.items() if c == best_count]
        if len(leaders) > 1:
            # Ничья по частоте — строгого большинства быть не может
            return None

        threshold = len(non_none) // 2 + 1  # строго >50%
        if best_count < threshold:
            return None

        # Возвращаем исходное (не нормализованное) значение
        for v, norm in zip(non_none, norms):
            if norm == best_norm:
                return v
        return None  # недостижимо, но на всякий случай

    @staticmethod
    def _vote_list(lists: list) -> Optional[list]:
        """
        Голосование для массива (напр. items).

        Собирает все элементы из всех списков, канонизирует каждый элемент
        (dict → JSON со sorted keys; скаляр → само значение) и сохраняет те,
        что встречаются в строгом большинстве (>50%) результатов.
        Возвращает None, если не осталось ни одного элемента.
        """
        non_empty = [lst for lst in lists if isinstance(lst, list) and len(lst) > 0]
        if not non_empty:
            return None

        n = len(non_empty)
        threshold = n // 2 + 1  # строго >50%

        # canonical -> (original element, count)
        seen: Dict[str, Any] = {}
        for lst in non_empty:
            present: set = set()
            for el in lst:
                key = LlamaClient._normalize_list(el)
                if key in present:
                    continue  # один элемент в одном списке — один голос
                present.add(key)
                rec = seen.get(key)
                if rec is None:
                    seen[key] = [el, 1]
                else:
                    rec[1] += 1

        merged = [rec[0] for rec in seen.values() if rec[1] >= threshold]
        return merged if merged else None

    @staticmethod
    def _vote_nested_dict(values: list) -> Optional[dict]:
        """
        Голосование для вложенного dict: по каждому под-ключу независимо.
        Если под-значение само dict — рекурсивно.
        Скалярные значения среди dict-голосов игнорируются (с логированием).
        Возвращает None, если после голосования не осталось ни одного ключа.
        """
        dicts: list = []
        ignored = 0
        for v in values:
            if isinstance(v, dict):
                dicts.append(v)
            elif v is not None:
                ignored += 1

        if not dicts:
            return None
        if ignored:
            logger.debug("_vote_nested_dict: проигнорировано %d не-dict значений", ignored)

        all_sub_keys: set = set()
        for d in dicts:
            all_sub_keys.update(d.keys())

        result: dict = {}
        for sk in all_sub_keys:
            sub_vals = [d.get(sk) for d in dicts]
            if any(isinstance(sv, dict) for sv in sub_vals if sv is not None):
                sub_voted = LlamaClient._vote_nested_dict(sub_vals)
            elif any(isinstance(sv, list) for sv in sub_vals if sv is not None):
                sub_voted = LlamaClient._vote_list(sub_vals)
            else:
                sub_voted = LlamaClient._vote_value(sub_vals)
            if sub_voted is not None:
                result[sk] = sub_voted

        return result if result else None

    @staticmethod
    def _vote_results(results: list, doc_type: str, file_name: str) -> dict:
        """
        Сравнить N результатов и выбрать значения со строгим большинством (>50%).
        Для вложенных dict — голосование по каждому под-ключу.
        Ключи без строгого большинства отбрасываются.
        """
        base = {"docType": doc_type, "fileName": file_name}

        if not results:
            return {**base, "parseError": "Нет результатов"}

        valid = [r for r in results if isinstance(r, dict) and "parseError" not in r]
        if not valid:
            return {**base, "parseError": "Все проходы завершились ошибкой"}

        # Быстрый путь: все результаты идентичны
        jsons = [json.dumps(r, sort_keys=True, ensure_ascii=False, default=str) for r in valid]
        if len(set(jsons)) == 1:
            return {**valid[0], **base}  # docType/fileName гарантированно наши

        all_keys: set = set()
        for r in valid:
            all_keys.update(r.keys())

        voted: dict = {}
        for key in all_keys:
            values = [r.get(key) for r in valid]
            if any(isinstance(v, dict) for v in values if v is not None):
                out = LlamaClient._vote_nested_dict(values)
            else:
                out = LlamaClient._vote_value(values)

            if out is None:
                logger.debug("_vote_results: ключ %r отброшен — нет строгого большинства", key)
                continue
            voted[key] = out

        return {**voted, **base}

    @staticmethod
    def _token_stats_sentence(result: dict) -> str:
        """Отдельное предложение со статистикой токенов нейросети по документу."""
        stats = result.get("tokenStats")
        if not stats:
            return ""
        prompt_tokens = stats.get("promptTokens", 0)
        completion_tokens = stats.get("completionTokens", 0)
        return (
            f"\n🔢 Токены нейросети: отправлено {prompt_tokens}, "
            f"получено {completion_tokens} (всего {prompt_tokens + completion_tokens})."
        )


# ---------------------------------------------------------------------------
# Формирование сообщений для чата по типу документа
# ---------------------------------------------------------------------------

def _format_upd(file_name: str, r: dict) -> str:
    lines = [f"📄 *УПД* — {file_name}"]
    if r.get("docNumber"):
        lines.append(f"№ {r['docNumber']} от {r.get('docDate', '—')}")
    if r.get("supplier"):
        s = r["supplier"]
        lines.append(f"🏢 Поставщик: {s.get('name', '—')} (ИНН {s.get('inn', '—')})")
    if r.get("buyer"):
        b = r["buyer"]
        lines.append(f"🏢 Покупатель: {b.get('name', '—')} (ИНН {b.get('inn', '—')})")
    if r.get("items"):
        lines.append("📋 Позиции:")
        for i, item in enumerate(r["items"][:10], 1):
            lines.append(
                f"  {i}. {item.get('name', '—')} — "
                f"{item.get('quantity', '—')} {item.get('unit', '')} × "
                f"{item.get('price', '—')} = {item.get('amount', '—')}"
            )
        if len(r["items"]) > 10:
            lines.append(f"  ... и ещё {len(r['items']) - 10}")
    if r.get("totalWithVat") is not None:
        lines.append(f"💰 Итого с НДС: {r['totalWithVat']} руб.")
    elif r.get("totalWithoutVat") is not None:
        lines.append(f"💰 Итого без НДС: {r['totalWithoutVat']} руб. "
                     f"(НДС: {r.get('totalVat', '—')})")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_invoice(file_name: str, r: dict) -> str:
    lines = [f"📄 *Счёт-фактура* — {file_name}"]
    if r.get("docNumber"):
        lines.append(f"№ {r['docNumber']} от {r.get('docDate', '—')}")
    if r.get("supplier"):
        s = r["supplier"]
        lines.append(f"🏢 Продавец: {s.get('name', '—')} (ИНН {s.get('inn', '—')})")
    if r.get("buyer"):
        b = r["buyer"]
        lines.append(f"🏢 Покупатель: {b.get('name', '—')} (ИНН {b.get('inn', '—')})")
    if r.get("ground"):
        lines.append(f"📎 Основание: {r['ground']}")
    if r.get("items"):
        lines.append("📋 Позиции:")
        for i, item in enumerate(r["items"][:10], 1):
            lines.append(
                f"  {i}. {item.get('name', '—')} — "
                f"{item.get('quantity', '—')} {item.get('unit', '')} × "
                f"{item.get('price', '—')} = {item.get('amount', '—')}"
            )
        if len(r["items"]) > 10:
            lines.append(f"  ... и ещё {len(r['items']) - 10}")
    if r.get("totalWithVat") is not None:
        lines.append(f"💰 Итого с НДС: {r['totalWithVat']} руб.")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_payment_order(file_name: str, r: dict) -> str:
    lines = [f"💵 *Счёт на оплату* — {file_name}"]
    if r.get("docNumber"):
        lines.append(f"№ {r['docNumber']} от {r.get('docDate', '—')}")
    if r.get("seller"):
        s = r["seller"]
        lines.append(f"🏢 Продавец: {s.get('name', '—')} (ИНН {s.get('inn', '—')})")
    if r.get("payer"):
        p = r["payer"]
        lines.append(f"👤 Плательщик: {p.get('name', '—')} (ИНН {p.get('inn', '—')})")
    if r.get("paymentPurpose"):
        lines.append(f"📋 Назначение: {r['paymentPurpose']}")
    if r.get("amount") is not None:
        lines.append(f"💰 Сумма: {r['amount']} руб.")
    if r.get("paymentDeadline"):
        lines.append(f"⏰ Срок оплаты: {r['paymentDeadline']}")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_contract(file_name: str, r: dict) -> str:
    lines = [f"📜 *Договор* — {file_name}"]
    if r.get("docNumber"):
        lines.append(f"№ {r['docNumber']} от {r.get('docDate', '—')}")
    if r.get("place"):
        lines.append(f"📍 {r['place']}")
    if r.get("party1"):
        p = r["party1"]
        lines.append(f"🏢 {p.get('role', 'Сторона 1')}: {p.get('name', '—')} (ИНН {p.get('inn', '—')})")
    if r.get("party2"):
        p = r["party2"]
        lines.append(f"🏢 {p.get('role', 'Сторона 2')}: {p.get('name', '—')} (ИНН {p.get('inn', '—')})")
    if r.get("subject"):
        lines.append(f"📋 Предмет: {r['subject']}")
    if r.get("contractAmount") is not None:
        lines.append(f"💰 Сумма: {r['contractAmount']} руб.")
    if r.get("termStart") or r.get("termEnd"):
        lines.append(f"📅 Срок: {r.get('termStart', '—')} — {r.get('termEnd', '—')}")
    if r.get("paymentTerms"):
        lines.append(f"💳 Расчёты: {r['paymentTerms']}")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_act(file_name: str, r: dict) -> str:
    lines = [f"📋 *Акт выполненных работ* — {file_name}"]
    if r.get("docNumber"):
        lines.append(f"№ {r['docNumber']} от {r.get('docDate', '—')}")
    if r.get("contractRef"):
        lines.append(f"📎 По договору: {r['contractRef']}")
    if r.get("executor"):
        lines.append(f"🏢 Исполнитель: {r['executor'].get('name', '—')}")
    if r.get("customer"):
        lines.append(f"🏢 Заказчик: {r['customer'].get('name', '—')}")
    if r.get("periodFrom") or r.get("periodTo"):
        lines.append(f"📅 Период: {r.get('periodFrom', '—')} — {r.get('periodTo', '—')}")
    if r.get("items"):
        lines.append("📋 Выполненные работы:")
        for i, item in enumerate(r["items"][:10], 1):
            lines.append(
                f"  {i}. {item.get('description', '—')} — "
                f"{item.get('quantity', '—')} {item.get('unit', '')} × "
                f"{item.get('price', '—')} = {item.get('amount', '—')}"
            )
        if len(r["items"]) > 10:
            lines.append(f"  ... и ещё {len(r['items']) - 10}")
    if r.get("totalAmount") is not None:
        lines.append(f"💰 Итого: {r['totalAmount']} руб.")
    if r.get("hasDisputes"):
        lines.append(f"⚠️ Претензии: {r.get('disputeText', '—')}")
    elif r.get("hasDisputes") is False:
        lines.append("✅ Претензий нет")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_letter(file_name: str, r: dict) -> str:
    lines = [f"✉️ *Информационное письмо* — {file_name}"]
    if r.get("docNumber"):
        lines.append(f"№ {r['docNumber']} от {r.get('docDate', '—')}")
    if r.get("sender"):
        s = r["sender"]
        sender_str = s.get("organization", "")
        if s.get("person"):
            sender_str = f"{s['person']} ({sender_str})"
        if sender_str:
            lines.append(f"📤 От: {sender_str}")
    if r.get("recipient"):
        rc = r["recipient"]
        recv_str = rc.get("organization", "")
        if rc.get("person"):
            recv_str = f"{rc['person']} ({recv_str})"
        if recv_str:
            lines.append(f"📥 Кому: {recv_str}")
    if r.get("subject"):
        lines.append(f"📌 Тема: {r['subject']}")
    if r.get("summary"):
        lines.append(f"📋 Кратко: {r['summary']}")
    if r.get("requiresAction"):
        lines.append("⚡ Требует действий")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_personal(file_name: str, r: dict) -> str:
    sub_labels = {
        "passport": "Паспорт РФ",
        "oms_policy": "Полис ОМС",
        "snils": "СНИЛС",
        "driver_license": "Водительское удостоверение",
        "certificate": "Сертификат",
        "other": "Личный документ",
    }
    sub = r.get("subType", "other")
    lines = [f"🔒 *Личный документ* — {file_name}"]
    lines.append(f"Тип: {sub_labels.get(sub, sub)}")
    if r.get("holderName"):
        lines.append(f"ФИО: {r['holderName']}")
    if r.get("docNumber"):
        num = r["docNumber"]
        if r.get("series"):
            num = f"{r['series']} {num}"
        lines.append(f"Номер: {num}")
    if r.get("issuedBy"):
        lines.append(f"Кем выдан: {r['issuedBy']}")
    if r.get("issueDate"):
        lines.append(f"Дата выдачи: {r['issueDate']}")
    if r.get("expiryDate"):
        lines.append(f"Действует до: {r['expiryDate']}")
    pd = r.get("personalData")
    if pd:
        if pd.get("dateOfBirth"):
            lines.append(f"Дата рождения: {pd['dateOfBirth']}")
        if pd.get("placeOfBirth"):
            lines.append(f"Место рождения: {pd['placeOfBirth']}")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


def _format_unknown(file_name: str, r: dict) -> str:
    lines = [f"📎 *Документ* — {file_name}"]
    if r.get("detectedType"):
        lines.append(f"Предполагаемый тип: {r['detectedType']}")
    if r.get("summary"):
        lines.append(f"📋 {r['summary']}")
    if r.get("extractedText"):
        text = r["extractedText"][:500]
        if len(r["extractedText"]) > 500:
            text += "..."
        lines.append(f"Текст:\n{text}")
    if r.get("notes"):
        lines.append(f"📝 {r['notes']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# DocumentBot
# ---------------------------------------------------------------------------

class DocumentBot(Bot):
    """
    Бот, который периодически опрашивает заданную папку на Nextcloud,
    обрабатывает новые файлы (PDF / изображения) через llama.cpp server
    и отправляет структурированный результат в указанный чат.
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
        self.llama = LlamaClient(config.LLAMA_SERVER_URL, ocr_url=config.OCR_SERVICE_URL, steps_count=1)

        # Репозиторий документов (PostgreSQL)
        self.doc_repo = DocumentRepository()
        # Привязываем репозиторий к LlamaClient — для поиска контрагентов по ИНН/КПП
        self.llama.set_repo(self.doc_repo)

        # Трекер обработанных файлов (href → timestamp)
        self._processed: Dict[str, float] = {}

        # Флаг запуска background-задачи
        self._task: Optional[asyncio.Task] = None

        # Регистрация команд (как в других ботах — в __init__)
        self._register_commands()

    def _register_commands(self):
        """Регистрация команд бота."""
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

    def start(self) -> None:
        """Запустить фоновый опрос папки."""
        if self._task and not self._task.done():
            return  # Уже запущен
        self._task = asyncio.create_task(self._poll_loop())
        # Подключение к БД (схема создаётся автоматически)
        asyncio.create_task(self._init_db())

    async def _init_db(self) -> None:
        """Подключиться к PostgreSQL и применить схему."""
        try:
            await self.doc_repo.connect()
        except Exception as e:
            logger.info(f"[DocumentBot] Ошибка подключения к PostgreSQL: {e}")

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
                logger.info(f"[DocumentBot] Ошибка при опросе: {e}")

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
            logger.info(f"[DocumentBot] Не удалось получить список файлов: {e}")
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

            # Пропускаем файлы-результаты в подпапке result/
            if "/result/" in rel_path:
                continue

            # Проверка типа источника
            source_type = _detect_source_type(file_name)
            if source_type == "unknown":
                logger.info(f"[DocumentBot] Пропускаю неизвестный тип: {file_name}")
                continue

            logger.info(f"[DocumentBot] Найден новый файл: {file_name} ({source_type})")
            await self._process_and_notify(rel_path, file_name, source_type)

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
                                  source_type: str) -> None:
        """Скачать файл, обработать через llama.cpp, отправить результат в чат."""
        try:
            # Скачать содержимое файла
            file_data = self.nc_client.download_file_content(
                rel_path, encode_path=True
            )
        except Exception as e:
            msg = f"❌ Ошибка скачивания {file_name}: {e}"
            logger.info(f"[DocumentBot] {msg}")
            await self._notify(msg)
            return

        try:
            result = await self.llama.process_document(file_name, file_data, source_type)
        except Exception as e:
            msg = f"❌ Ошибка обработки {file_name} через llama.cpp: {e}"
            logger.info(f"[DocumentBot] {msg}")
            await self._notify(msg)
            return

        # Сохранить сырые данные в PostgreSQL — на проверку пользователем через веб-интерфейс
        try:
            await self.doc_repo.save_raw_document(result)
        except Exception as e:
            logger.info(f"[DocumentBot] Ошибка сохранения в БД: {e}")

        # Сформировать сообщение для чата
        doc_type = result.get("docType", DOCUMENT_TYPE_UNKNOWN)
        chat_msg = self._format_result(file_name, doc_type, result)

        # Сохранить JSON-структуру результата и отправить вложением
        await self._send_result_with_attachment(file_name, result, chat_msg)

    @staticmethod
    def _format_result(file_name: str, doc_type: str, result: dict) -> str:
        """Сформировать читаемое сообщение из результата обработки."""

        if "parseError" in result:
            msg = (
                f"📄 *{file_name}*\n"
                f"⚠️ Обработка завершена с предупреждением:\n"
                f"{result.get('parseError', 'unknown error')}\n\n"
                f"Сырой ответ:\n{result.get('rawResponse', 'N/A')[:500]}"
            )
            return msg + LlamaClient._token_stats_sentence(result)

        formatters = {
            DOCUMENT_TYPE_UPD: _format_upd,
            DOCUMENT_TYPE_INVOICE: _format_invoice,
            DOCUMENT_TYPE_PAYMENT_ORDER: _format_payment_order,
            DOCUMENT_TYPE_CONTRACT: _format_contract,
            DOCUMENT_TYPE_ACT: _format_act,
            DOCUMENT_TYPE_LETTER: _format_letter,
            DOCUMENT_TYPE_PERSONAL: _format_personal,
            DOCUMENT_TYPE_UNKNOWN: _format_unknown,
        }

        formatter = formatters.get(doc_type, _format_unknown)
        return formatter(file_name, result) + LlamaClient._token_stats_sentence(result)

    async def _notify(self, message: str) -> None:
        """Отправить сообщение в заданный чат."""
        if not self.chat_room:
            logger.info(f"[DocumentBot] (нет chat_room) {message}")
            return

        try:
            await self.send_to_nextcloud(self.chat_room, message, silent=True)
        except Exception as e:
            logger.info(f"[DocumentBot] Ошибка отправки в чат: {e}")

    async def _upload_json_to_nextcloud(self, file_name: str, data: dict) -> str | None:
        """
        Сериализовать JSON-структуру результата и загрузить файл
        на Nextcloud через WebDAV (create_file). Вернуть путь к файлу.
        """
        # Безопасное имя без расширений → .json
        base = os.path.splitext(os.path.basename(urllib.parse.unquote(file_name)))[0]
        safe_base = "".join(c if c.isalnum() or c in " -_" else "_" for c in base)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        json_file_name = f"{safe_base}_{timestamp}.json"

        # Путь в Nextcloud рядом с watched-папкой
        remote_dir = config.DOCUMENT_WATCH_DIR.rstrip("/") + "/result"

        try:
            # Сериализация в JSON (utf-8, ensure_ascii=False)
            json_content = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

            # Убедиться, что удалённая директория существует
            self.nc_client.create_directory_recursive(remote_dir)

            # Создание файла на Nextcloud с заданным именем
            self.nc_client.create_file(remote_dir, json_file_name, json_content)
            logger.info(f"[DocumentBot] JSON загружен: {remote_dir}/{json_file_name}")
            return f"{remote_dir}/{json_file_name}"
        except Exception as e:
            logger.info(f"[DocumentBot] Ошибка загрузки JSON: {e}")
            return None

    async def _send_result_with_attachment(self, file_name: str,
                                           result: dict,
                                           chat_msg: str) -> None:
        """
        Отправить текстовое описание результата + JSON-файл вложением в чат.
        1. Загрузить JSON на Nextcloud.
        2. Отправить текстовое сообщение.
        3. Прикрепить файл к чату через share-API.
        """
        if not self.chat_room:
            logger.info(f"[DocumentBot] (нет chat_room) {chat_msg}")
            return

        # Шаг 1 — загрузить JSON
        remote_path = await self._upload_json_to_nextcloud(file_name, result)

        # Шаг 2 — отправить текстовое сообщение
        try:
            await self.send_to_nextcloud(self.chat_room, chat_msg, silent=True)
        except Exception as e:
            logger.info(f"[DocumentBot] Ошибка отправки сообщения: {e}")

        # Шаг 3 — прикрепить файл в чат
        if remote_path:
            try:
                await self._share_file_in_chat(self.chat_room, remote_path, caption=chat_msg)
            except Exception as e:
                logger.info(f"[DocumentBot] Ошибка прикрепления файла: {e}")

    async def _share_file_in_chat(self, room_token: str, file_path: str,
                                  caption: str = "") -> None:
        """
        Отправить файл в чат через files_sharing API (shareType=10).

        Использует POST /ocs/v2.php/apps/files_sharing/api/v1/shares
        с shareType=10 (Talk conversation).
        """
        try:
            import secrets
            reference_id = secrets.token_hex(32)

            self.nc_client.share_file_to_chat(
                room_token=room_token,
                file_path=file_path,
                caption=caption,
                reference_id=reference_id,
                silent=True,
            )
            logger.info(f"[DocumentBot] Файл прикреплён: {file_path}")
        except Exception as e:
            # Fallback — попытаться через get_file_id + chat/share
            logger.info(f"[DocumentBot] files_sharing fallback: {e}")
            try:
                file_id = self.nc_client.get_file_id(file_path, encode_path=True)
                await self._share_file_by_id(room_token, file_id)
            except Exception as e2:
                raise Exception(f"Fallback тоже не сработал: {e2}") from e

    async def _share_file_by_id(self, room_token: str, file_id: int) -> None:
        """Fallback — прикрепить файл через chat/{token}/share (objectType=Files, objectId=file_id)."""
        url = f"{self.nc_url}/ocs/v2.php/apps/spreed/api/v1/chat/{room_token}/share"

        payload = {
            "objectType": "Files",
            "objectId": str(file_id),
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                json=payload,
                headers={
                    "OCS-APIRequest": "true",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                timeout=30.0,
            )
            if resp.status_code not in (200, 201):
                logger.info(f"[DocumentBot] chat/share fallback failed: {resp.status_code} {resp.text[:300]}")
            else:
                logger.info(f"[DocumentBot] Файл прикреплён (id={file_id}) через fallback")

    # -----------------------------------------------------------------------
    # Команды бота (для управления через чат)
    # -----------------------------------------------------------------------

    async def handle_help(self, args: list = None, user_id: str = None,
                          room_token: str = None) -> str:
        return (
            "🤖 *DocumentBot* — автоматическая обработка документов\n\n"
            "• Мониторит папку на Nextcloud на новые файлы (PDF, изображения)\n"
            "• Определяет тип: УПД, счёт-фактура, счёт на оплату, договор, акт, письмо, личный документ\n"
            "• Извлекает структурированные данные\n"
            "• Отправляет результат в заданный чат\n\n"
            "Команды:\n"
            "• `статус` — информация о мониторинге\n"
            "• `пауза` — приостановить мониторинг\n"
            "• `старт` — возобновить мониторинг\n"
        )

    async def handle_status(self, args: list = None, user_id: str = None,
                            room_token: str = None) -> str:
        return (
            f"📊 *Статус DocumentBot*\n\n"
            f"• Папка: `{self.watch_dir}`\n"
            f"• Чат для результатов: `{self.chat_room}`\n"
            f"• Интервал опроса: {self.poll_interval}с\n"
            f"• Llama server: `{config.LLAMA_SERVER_URL}`\n"
            f"• PostgreSQL: `{config.POSTGRES_DSN.split('@')[-1] if config.POSTGRES_DSN else 'не задан'}`\n"
            f"• Обработано файлов (сессия): {len(self._processed)}\n"
            f"• Активен: {'✅' if self._task and not self._task.done() else '❌'}\n"
        )

    async def handle_pause(self, args: list = None, user_id: str = None,
                           room_token: str = None) -> str:
        self.stop()
        return "⏸ Мониторинг приостановлен"

    async def handle_start(self, args: list = None, user_id: str = None,
                           room_token: str = None) -> str:
        self.start()
        return "▶️ Мониторинг возобновлён"
