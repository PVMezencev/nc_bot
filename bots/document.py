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
from typing import Dict, Any, List, Optional, Tuple

import config
import httpx

from bots.common import Bot
from nextcloud.nextcloudapi import NextcloudClient
from utilites import pdf_to_jpg_base64

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
- Возвращай ТОЛЬКО валидный JSON, без markdown-обёртки ```json``` и без пояснений.
- Если документ многостраничный — объедини данные со всех страниц.
- Заполни все поля, которые удалось определить. Пропускай поля, которых нет в документе.
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
Ты — классификатор документов. Определи тип приложенного документа.

{types_description}

Ответь ТОЛЬКО одним из значений docType:
{type_values}

Без пояснений, без markdown — только одно значение.
""".format(
    types_description=_DOCUMENT_TYPES_DESCRIPTION,
    type_values=json.dumps(list(SCHEMA_MAP.keys()), ensure_ascii=False, indent=2),
)


# ---------------------------------------------------------------------------
# Llama.cpp HTTP клиент
# ---------------------------------------------------------------------------

class LlamaClient:
    """Клиент для llama.cpp server (OpenAI-compatible API)."""

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")
        self._classify_prompt = _CLASSIFY_PROMPT

    async def _completion(self, client: httpx.AsyncClient, messages: list) -> str:
        """Вызвать /v1/chat/completions на llama.cpp server."""
        url = f"{self.server_url}/v1/chat/completions"
        payload = {
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        resp = await client.post(url, json=payload, timeout=360.0)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

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

        result = await self._completion(client, messages)
        result = result.strip().strip("`").strip()

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

    async def extract_document_data(self, client: httpx.AsyncClient,
                                    images_base64: List[str],
                                    file_name: str,
                                    doc_type: str) -> dict:
        """Извлечь структурированные данные согласно схеме для типа."""
        system_prompt = build_system_prompt(doc_type)
        label = DOCUMENT_TYPE_LABELS.get(doc_type, doc_type)

        content_parts = []
        for b64 in images_base64:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
            })
        content_parts.append({
            "type": "text",
            "text": (
                f"Файл: {file_name}\n"
                f"Документ классифицирован как: {label}\n"
                f"Извлеки структурированные данные согласно JSON-схеме для типа \"{doc_type}\".\n"
                f"Возвращай ТОЛЬКО валидный JSON."
            )
        })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_parts}
        ]

        result_text = await self._completion(client, messages)

        # Очистка markdown-обёртки
        result_text = result_text.strip()
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

    async def process_document(self, file_name: str, file_data: bytes,
                               source_type: str) -> dict:
        """
        Полный пайплайн: классификация → извлечение данных.
        source_type: 'pdf' или 'image' — формат источника.
        """
        # Конвертируем в список изображений base64
        if source_type == "pdf":
            images_base64 = pdf_to_jpg_base64(file_data)
            # pdf_to_jpg_base64 возвращает один строковый base64 (объединённые страницы)
            if isinstance(images_base64, str):
                images_base64 = [images_base64]
        else:
            # Изображение — кодировать как base64
            b64 = base64.b64encode(file_data).decode("ascii")
            images_base64 = [b64]

        async with httpx.AsyncClient() as client:
            # Шаг 1: классификация
            doc_type = await self.classify_document(client, images_base64, file_name)
            print(f"[LlamaClient] Определён тип: {doc_type} ({DOCUMENT_TYPE_LABELS.get(doc_type)})")

            # Шаг 2: извлечение данных
            result = await self.extract_document_data(client, images_base64, file_name, doc_type)
            return result


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
        self.llama = LlamaClient(config.LLAMA_SERVER_URL)

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

            # Пропускаем файлы-результаты в подпапке result/
            if "/result/" in rel_path:
                continue

            # Проверка типа источника
            source_type = _detect_source_type(file_name)
            if source_type == "unknown":
                print(f"[DocumentBot] Пропускаю неизвестный тип: {file_name}")
                continue

            print(f"[DocumentBot] Найден новый файл: {file_name} ({source_type})")
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
            print(f"[DocumentBot] {msg}")
            await self._notify(msg)
            return

        try:
            result = await self.llama.process_document(file_name, file_data, source_type)
        except Exception as e:
            msg = f"❌ Ошибка обработки {file_name} через llama.cpp: {e}"
            print(f"[DocumentBot] {msg}")
            await self._notify(msg)
            return

        # Сформировать сообщение для чата
        doc_type = result.get("docType", DOCUMENT_TYPE_UNKNOWN)
        chat_msg = self._format_result(file_name, doc_type, result)

        # Сохранить JSON-структуру результата и отправить вложением
        await self._send_result_with_attachment(file_name, result, chat_msg)

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
        return formatter(file_name, result)

    async def _notify(self, message: str) -> None:
        """Отправить сообщение в заданный чат."""
        if not self.chat_room:
            print(f"[DocumentBot] (нет chat_room) {message}")
            return

        try:
            await self.send_to_nextcloud(self.chat_room, message, silent=True)
        except Exception as e:
            print(f"[DocumentBot] Ошибка отправки в чат: {e}")

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
            print(f"[DocumentBot] JSON загружен: {remote_dir}/{json_file_name}")
            return f"{remote_dir}/{json_file_name}"
        except Exception as e:
            print(f"[DocumentBot] Ошибка загрузки JSON: {e}")
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
            print(f"[DocumentBot] (нет chat_room) {chat_msg}")
            return

        # Шаг 1 — загрузить JSON
        remote_path = await self._upload_json_to_nextcloud(file_name, result)

        # Шаг 2 — отправить текстовое сообщение
        try:
            await self.send_to_nextcloud(self.chat_room, chat_msg, silent=True)
        except Exception as e:
            print(f"[DocumentBot] Ошибка отправки сообщения: {e}")

        # Шаг 3 — прикрепить файл в чат
        if remote_path:
            try:
                await self._share_file_in_chat(self.chat_room, remote_path, caption=chat_msg)
            except Exception as e:
                print(f"[DocumentBot] Ошибка прикрепления файла: {e}")

    async def _share_file_in_chat(self, room_token: str, file_path: str,
                                  caption: str = "") -> None:
        """Отправить файл в чат через files_sharing API (shareType=10)."""
        import secrets
        reference_id = secrets.token_hex(32)

        self.nc_client.share_file_to_chat(
            room_token=room_token,
            file_path=file_path,
            caption=caption,
            reference_id=reference_id,
            silent=True,
        )
        print(f"[DocumentBot] Файл прикреплён: {file_path}")



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
