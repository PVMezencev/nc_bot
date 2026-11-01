"""
Хранилище документов (PostgreSQL).

Заполняет таблицы из JSON-дампа, который DocumentBot получает
от llama.cpp при обработке сканированных документов.

Структура таблиц — в db/schema.sql.

Контрагенты не дублируются:
  • при наличии ИНН — поиск по (INN, KPP), иначе по (INN);
  • без ИНН — поиск по (name, INN).
При совпадении — обновляем недостающие поля существующей записи.
"""

import asyncio
import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import asyncpg

import config

# ---------------------------------------------------------------------------
# Парсинг дат (модель отдаёт DD.MM.YYYY)
# ---------------------------------------------------------------------------


def parse_date(value: Any) -> Optional[date]:
    """Преобразовать строку DD.MM.YYYY (или YYYY-MM-DD) в date. None — если не удалось."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def to_number(value: Any) -> Optional[float]:
    """Безопасное преобразование в число (модель может вернуть строку)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def to_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "да", "yes")


def _clean_inn_kpp(v: Any) -> Optional[str]:
    """ИНН/КПП — чистые цифры без пробелов и тире."""
    s = to_text(v)
    if not s:
        return None
    s = "".join(c for c in s if c.isdigit())
    return s or None


# ---------------------------------------------------------------------------
# DocumentRepository
# ---------------------------------------------------------------------------


class DocumentRepository:
    """Асинхронный репозиторий документов (asyncpg)."""

    def __init__(self, dsn: str = None):
        self.dsn = dsn or config.POSTGRES_DSN
        self.enabled = bool(self.dsn)
        self._pool: Optional[asyncpg.Pool] = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Подключение
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if not self.enabled:
            print("[DocumentRepository] POSTGRES_DSN не задан — БД отключена")
            return
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=5)
            await self._init_schema()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _init_schema(self) -> None:
        """Применить db/schema.sql (идемпотентно)."""
        schema_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "db", "schema.sql",
        )
        with open(schema_path, "r", encoding="utf-8") as f:
            schema_sql = f.read()
        async with self._pool.acquire() as conn:
            await conn.execute(schema_sql)
        print("[DocumentRepository] Схема БД применена")

    # ------------------------------------------------------------------
    # «Сырые» данные от бота (raw_documents)
    # ------------------------------------------------------------------

    async def save_raw_document(self, result: Dict[str, Any]) -> Optional[int]:
        """Сохранить сырой JSON-дамп от бота со статусом pending."""
        if self._pool is None or not result:
            return None
        doc_type = to_text(result.get("docType")) or "unknown"
        file_name = to_text(result.get("fileName")) or "unknown"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO raw_documents (doc_type, file_name, raw_data, status)
                VALUES ($1, $2, $3, 'pending')
                RETURNING id
                """,
                doc_type, file_name, json.dumps(result, ensure_ascii=False),
            )
        print(f"[DocumentRepository] Сырой документ сохранён: {file_name} ({doc_type}) → id={row['id']}")

        # Уведомить веб-интерфейс (SSE) о новом сыром документе
        try:
            from web.app import notify_new_raw_document
            notify_new_raw_document(row["id"], file_name, doc_type)
        except Exception:
            pass

        return row["id"]

    async def get_raw_document(self, raw_id: int) -> Optional[Dict[str, Any]]:
        """Получить сырой документ по id (raw_data — dict)."""
        if self._pool is None:
            return None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM raw_documents WHERE id = $1", raw_id
            )
        if not row:
            return None
        data = dict(row)
        if isinstance(data.get("raw_data"), str):
            data["raw_data"] = json.loads(data["raw_data"])
        return data

    async def list_raw_documents(self, status: Optional[str] = None,
                                 limit: int = 100) -> List[Dict[str, Any]]:
        """Список сырых документов (по умолчанию — все, newest first)."""
        if self._pool is None:
            return []
        async with self._pool.acquire() as conn:
            if status:
                rows = await conn.fetch(
                    "SELECT id, doc_type, file_name, status, created_at, checked_at, processed_doc_id "
                    "FROM raw_documents WHERE status = $1 ORDER BY created_at DESC LIMIT $2",
                    status, limit,
                )
            else:
                rows = await conn.fetch(
                    "SELECT id, doc_type, file_name, status, created_at, checked_at, processed_doc_id "
                    "FROM raw_documents ORDER BY created_at DESC LIMIT $1", limit,
                )
        return [dict(r) for r in rows]

    async def count_raw_documents(self, status: Optional[str] = None) -> int:
        """Количество сырых документов по статусу (None — все)."""
        if self._pool is None:
            return 0
        async with self._pool.acquire() as conn:
            if status:
                return await conn.fetchval(
                    "SELECT count(*) FROM raw_documents WHERE status = $1", status
                )
            return await conn.fetchval("SELECT count(*) FROM raw_documents")

    async def mark_raw_document(self, raw_id: int, status: str,
                                processed_doc_id: Optional[int] = None) -> None:
        """Пометить сырой документ: approved / rejected."""
        if self._pool is None:
            return
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE raw_documents
                SET status = $2, checked_at = now(), processed_doc_id = $3
                WHERE id = $1
                """,
                raw_id, status, processed_doc_id,
            )

    # ------------------------------------------------------------------
    # Контрагенты (upsert без дублей)
    # ------------------------------------------------------------------

    async def upsert_counterparty(self, conn, data: Dict[str, Any]) -> Optional[int]:
        """
        Найти или создать контрагента.

        data — dict из JSON-дампа (supplier/buyer/seller/payer/party1/party2/
        executor/customer и т.п.) с ключами: name, shortName, inn, kpp, ogrn,
        address, bank, bik, accountNumber, correspondentAccount, representative.
        """
        if not data or not isinstance(data, dict):
            return None

        name = to_text(data.get("name")) or to_text(data.get("organization"))
        inn = _clean_inn_kpp(data.get("inn"))
        kpp = _clean_inn_kpp(data.get("kpp"))
        if not name and not inn:
            return None

        # --- поиск существующего ---
        counterparty_id: Optional[int] = None
        if inn:
            row = await conn.fetchrow(
                "SELECT id FROM counterparties WHERE inn = $1 AND (kpp = $2 OR kpp IS NULL) "
                "ORDER BY (kpp IS NULL) DESC LIMIT 1",
                inn, kpp,
            )
            if row:
                counterparty_id = row["id"]
        if counterparty_id is None and name:
            row = await conn.fetchrow(
                "SELECT id FROM counterparties WHERE lower(name) = lower($1) AND (inn = $2 OR inn IS NULL)",
                name, inn,
            )
            if row:
                counterparty_id = row["id"]

        # --- создание или обновление ---
        fields = {
            "name": name,
            "short_name": to_text(data.get("shortName")),
            "inn": inn,
            "kpp": kpp,
            "ogrn": to_text(data.get("ogrn")),
            "address": to_text(data.get("address")),
            "bank": to_text(data.get("bank")),
            "bik": to_text(data.get("bik")),
            "account_number": to_text(data.get("accountNumber")),
            "correspondent_account": to_text(data.get("correspondentAccount")),
            "representative": to_text(data.get("representative")),
        }

        if counterparty_id is None:
            row = await conn.fetchrow(
                """
                INSERT INTO counterparties
                    (name, short_name, inn, kpp, ogrn, address, bank, bik,
                     account_number, correspondent_account, representative)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                *fields.values(),
            )
            counterparty_id = row["id"]
            print(f"[DocumentRepository] Новый контрагент: {name} (ИНН {inn}) → id={counterparty_id}")
        else:
            # обновляем только непустые поля
            updates = {k: v for k, v in fields.items() if v is not None}
            if updates:
                set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(updates))
                await conn.execute(
                    f"UPDATE counterparties SET {set_clause}, updated_at = now() WHERE id = ${len(updates) + 1}",
                    *updates.values(), counterparty_id,
                )

        return counterparty_id

    # ------------------------------------------------------------------
    # Сохранение результата обработки документа
    # ------------------------------------------------------------------

    async def save_document(self, result: Dict[str, Any]) -> Optional[int]:
        """
        Сохранить JSON-дамп документа в БД.

        result — dict, полученный от LlamaClient.extract_document_data().
        Возвращает id созданной строки documents (или None).
        """
        if self._pool is None:
            return None
        if not result or "parseError" in result:
            return None

        doc_type = to_text(result.get("docType")) or "unknown"
        file_name = to_text(result.get("fileName"))
        if not file_name:
            return None

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # --- базовые поля documents ---
                doc_number = to_text(result.get("docNumber"))

                # Повторная обработка того же файла (тот же номер/тип) — не дублируем,
                # обновляем данные существующей строки.
                existing = await conn.fetchrow(
                    "SELECT id FROM documents WHERE file_name = $1 AND doc_type = $2 "
                    "AND (doc_number = $3 OR (doc_number IS NULL AND $3 IS NULL))",
                    file_name, doc_type, doc_number,
                )
                if existing:
                    doc_id = existing["id"]
                    await conn.execute(
                        """
                        UPDATE documents SET
                            doc_date = $2, ground = $3, payment_purpose = $4, amount = $5,
                            payment_deadline = $6, place = $7, subject = $8, contract_amount = $9,
                            term_start = $10, term_end = $11, payment_terms = $12, key_obligations = $13,
                            contract_ref = $14, period_from = $15, period_to = $16,
                            total_without_vat = $17, total_vat = $18, total_with_vat = $19,
                            total_amount = $20, has_disputes = $21, dispute_text = $22,
                            letter_subject = $23, summary = $24, full_text = $25, requires_action = $26,
                            detected_type = $27, extracted_text = $28, key_fields = $29, notes = $30
                        WHERE id = $1
                        """,
                        doc_id,
                        parse_date(result.get("docDate")),
                        to_text(result.get("ground")),
                        to_text(result.get("paymentPurpose")),
                        to_number(result.get("amount")),
                        parse_date(result.get("paymentDeadline")),
                        to_text(result.get("place")),
                        to_text(result.get("subject")),
                        to_number(result.get("contractAmount")),
                        parse_date(result.get("termStart")),
                        parse_date(result.get("termEnd")),
                        to_text(result.get("paymentTerms")),
                        to_text(result.get("keyObligations")),
                        to_text(result.get("contractRef")),
                        parse_date(result.get("periodFrom")),
                        parse_date(result.get("periodTo")),
                        to_number(result.get("totalWithoutVat")),
                        to_number(result.get("totalVat")),
                        to_number(result.get("totalWithVat")),
                        to_number(result.get("totalAmount")),
                        to_bool(result.get("hasDisputes")),
                        to_text(result.get("disputeText")),
                        to_text(result.get("subject")),
                        to_text(result.get("summary")),
                        to_text(result.get("fullText")),
                        to_bool(result.get("requiresAction")),
                        to_text(result.get("detectedType")),
                        to_text(result.get("extractedText")),
                        json.dumps(result.get("keyFields"), ensure_ascii=False) if result.get("keyFields") else None,
                        to_text(result.get("notes")),
                    )
                    await conn.execute(
                        "DELETE FROM document_items WHERE document_id = $1", doc_id
                    )
                else:
                    doc_row = await conn.fetchrow(
                    """
                    INSERT INTO documents
                        (doc_type, file_name, doc_number, doc_date, ground,
                         payment_purpose, amount, payment_deadline,
                         place, subject, contract_amount, term_start, term_end,
                         payment_terms, key_obligations, contract_ref,
                         period_from, period_to,
                         total_without_vat, total_vat, total_with_vat, total_amount,
                         has_disputes, dispute_text,
                         letter_subject, summary, full_text, requires_action,
                         detected_type, extracted_text, key_fields, notes)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                            $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24,
                            $25, $26, $27, $28, $29, $30, $31, $32)
                    RETURNING id
                    """,
                    doc_type,
                    file_name,
                    to_text(result.get("docNumber")),
                    parse_date(result.get("docDate")),
                    to_text(result.get("ground")),
                    to_text(result.get("paymentPurpose")),
                    to_number(result.get("amount")),
                    parse_date(result.get("paymentDeadline")),
                    to_text(result.get("place")),
                    to_text(result.get("subject")),
                    to_number(result.get("contractAmount")),
                    parse_date(result.get("termStart")),
                    parse_date(result.get("termEnd")),
                    to_text(result.get("paymentTerms")),
                    to_text(result.get("keyObligations")),
                    to_text(result.get("contractRef")),
                    parse_date(result.get("periodFrom")),
                    parse_date(result.get("periodTo")),
                    to_number(result.get("totalWithoutVat")),
                    to_number(result.get("totalVat")),
                    to_number(result.get("totalWithVat")),
                    to_number(result.get("totalAmount")),
                    to_bool(result.get("hasDisputes")),
                    to_text(result.get("disputeText")),
                    to_text(result.get("subject")),
                    to_text(result.get("summary")),
                    to_text(result.get("fullText")),
                    to_bool(result.get("requiresAction")),
                    to_text(result.get("detectedType")),
                    to_text(result.get("extractedText")),
                    json.dumps(result.get("keyFields"), ensure_ascii=False) if result.get("keyFields") else None,
                    to_text(result.get("notes")),
                    )
                    doc_id = doc_row["id"]

                # --- контрагенты ---
                counterparty_keys = (
                    "supplier", "buyer", "seller", "payer",
                    "executor", "customer",
                )
                for key in counterparty_keys:
                    data = result.get(key)
                    if isinstance(data, dict):
                        await self.upsert_counterparty(conn, data)

                # --- стороны договора (party1/party2 с ролью) ---
                for key, role_key in (("party1", "party1"), ("party2", "party2")):
                    data = result.get(key)
                    if isinstance(data, dict):
                        cp_id = await self.upsert_counterparty(conn, data)
                        if cp_id:
                            await conn.execute(
                                """
                                INSERT INTO contract_parties (document_id, counterparty_id, role)
                                VALUES ($1, $2, $3)
                                ON CONFLICT (document_id, counterparty_id, role) DO NOTHING
                                """,
                                doc_id, cp_id, to_text(data.get("role")),
                            )

                # --- отправитель/получатель письма (тоже контрагенты) ---
                for key in ("sender", "recipient"):
                    data = result.get(key)
                    if isinstance(data, dict):
                        await self.upsert_counterparty(conn, data)

                # --- позиции ---
                items = result.get("items")
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        await conn.execute(
                            """
                            INSERT INTO document_items
                                (document_id, name, quantity, unit, price, amount, vat_rate, vat_amount)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                            """,
                            doc_id,
                            to_text(item.get("name")) or to_text(item.get("description")),
                            to_number(item.get("quantity")),
                            to_text(item.get("unit")),
                            to_number(item.get("price")),
                            to_number(item.get("amount")),
                            to_number(item.get("vatRate")),
                            to_number(item.get("vatAmount")),
                        )

                # --- личный документ ---
                if doc_type == "personal":
                    pd = result.get("personalData") or {}
                    await conn.execute(
                        """
                        INSERT INTO personal_documents
                            (document_id, sub_type, holder_name, series, issued_by,
                             issue_date, expiry_date, full_name, date_of_birth,
                             place_of_birth, address, gender)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                        ON CONFLICT (document_id) DO UPDATE SET
                            sub_type = EXCLUDED.sub_type,
                            holder_name = EXCLUDED.holder_name,
                            series = EXCLUDED.series,
                            issued_by = EXCLUDED.issued_by,
                            issue_date = EXCLUDED.issue_date,
                            expiry_date = EXCLUDED.expiry_date,
                            full_name = EXCLUDED.full_name,
                            date_of_birth = EXCLUDED.date_of_birth,
                            place_of_birth = EXCLUDED.place_of_birth,
                            address = EXCLUDED.address,
                            gender = EXCLUDED.gender
                        """,
                        doc_id,
                        to_text(result.get("subType")),
                        to_text(result.get("holderName")),
                        to_text(result.get("series")),
                        to_text(result.get("issuedBy")),
                        parse_date(result.get("issueDate")),
                        parse_date(result.get("expiryDate")),
                        to_text(pd.get("fullName")),
                        parse_date(pd.get("dateOfBirth")),
                        to_text(pd.get("placeOfBirth")),
                        to_text(pd.get("address")),
                        to_text(pd.get("gender")),
                    )

        print(f"[DocumentRepository] Документ сохранён: {file_name} ({doc_type}) → id={doc_id}")
        return doc_id
