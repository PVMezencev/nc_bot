"""
Веб-интерфейс проверки данных документов.

Страницы:
  /                 — главная: счётчик непроверенных записей + ссылки
  /raw              — список сырых записей (по умолчанию — непроверенные)
  /raw/{id}         — форма проверки (предзаполнена данными от бота)
  /raw/{id}/save    — сохранить (POST): запись в документы, статус approved
  /raw/{id}/reject  — отклонить (POST): статус rejected
  /documents        — список документов
  /documents/{id}   — просмотр/редактирование документа + позиций
  /counterparties   — справочник контрагентов
  /counterparties/{id} — редактирование контрагента
"""

import asyncio
import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

import config
from repo.postgres import DocumentRepository
from web.forms import FORM_SPECS, build_result_from_form, get_form_values

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "web" / "templates"))
templates.env.globals["doc_type_labels"] = {
    "upd": "УПД",
    "invoice": "Счёт-фактура",
    "payment_order": "Счёт на оплату",
    "contract": "Договор",
    "act": "Акт выполненных работ",
    "letter": "Информационное письмо",
    "personal": "Личный документ",
    "unknown": "Неопределён",
}

STATUS_LABELS = {
    "pending": ("⏳ на проверке", "badge-pending"),
    "approved": ("✅ сохранена", "badge-approved"),
    "rejected": ("❌ отклонена", "badge-rejected"),
}

# ---------------------------------------------------------------------------
# Шина уведомлений для SSE (новые сырые документы)
# ---------------------------------------------------------------------------

_notification_subscribers: List[asyncio.Queue] = []
_global_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
_shutdown_event: asyncio.Event = asyncio.Event()  # ставится при остановке приложения


def _notification_subscribers_queue_empty() -> bool:
    return _global_queue.empty()


def notify_new_raw_document(raw_id: int, file_name: str, doc_type: str) -> None:
    """Поставить событие о новом сыром документе в глобальную очередь."""
    event = {
        "type": "new_raw_document",
        "id": raw_id,
        "file_name": file_name,
        "doc_type": doc_type,
    }
    try:
        _global_queue.put_nowait(event)
    except asyncio.QueueFull:
        pass


@router.get("/events")
async def sse_events(request: Request):
    """
    SSE-поток уведомлений о новых сырых документах.
    Опрашивает БД каждые 5 секунд (бот может работать в другом процессе).
    При shutdown — немедленно завершается.
    """
    repo = get_repo(request)
    last_seen_id: int = 0

    # Начальная позиция — текущий max(id)
    if repo._pool:
        async with repo._pool.acquire() as conn:
            row = await conn.fetchval("SELECT COALESCE(MAX(id), 0) FROM raw_documents")
            last_seen_id = row or 0

    async def event_generator():
        nonlocal last_seen_id
        yield "retry: 5000\n\n"
        while True:
            # Проверить disconnect или shutdown
            if await request.is_disconnected() or _shutdown_event.is_set():
                break

            # Проверить новые записи в БД
            new_events = []
            if repo._pool:
                try:
                    async with repo._pool.acquire() as conn:
                        rows = await conn.fetch(
                            "SELECT id, file_name, doc_type FROM raw_documents "
                            "WHERE id > $1 AND status = 'pending' ORDER BY id",
                            last_seen_id,
                        )
                        for r in rows:
                            new_events.append({
                                "type": "new_raw_document",
                                "id": r["id"],
                                "file_name": r["file_name"],
                                "doc_type": r["doc_type"],
                            })
                            last_seen_id = r["id"]
                except Exception:
                    pass

            # Также проверить in-process уведомления (если бот в том же процессе)
            while not _notification_subscribers_queue_empty():
                try:
                    ev = _global_queue.get_nowait()
                    if ev["id"] > last_seen_id:
                        new_events.append(ev)
                        last_seen_id = ev["id"]
                except asyncio.QueueEmpty:
                    break

            for ev in new_events:
                yield f"event: new_raw_document\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

            if not new_events:
                yield ": keepalive\n\n"

            # Ждём 5 сек или shutdown (что наступит раньше)
            try:
                await asyncio.wait_for(_shutdown_event.wait(), timeout=5.0)
                break  # shutdown
            except asyncio.TimeoutError:
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def get_repo(request: Request) -> DocumentRepository:
    return request.app.state.doc_repo


def fmt_date(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (date, datetime)):
        return v.strftime("%d.%m.%Y")
    return str(v)


def fmt_num(v: Any) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@router.get("/api/pending-count")
async def pending_count_api(request: Request):
    """Текущее количество непроверенных записей (для JS-пересинхронизации)."""
    repo = get_repo(request)
    count = await repo.count_raw_documents("pending")
    return {"count": count}


# ---------------------------------------------------------------------------
# Главная
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    repo = get_repo(request)
    pending = await repo.count_raw_documents("pending")
    approved = await repo.count_raw_documents("approved")
    rejected = await repo.count_raw_documents("rejected")
    docs = await repo._pool.fetchval("SELECT count(*) FROM documents") if repo._pool else 0
    cps = await repo._pool.fetchval("SELECT count(*) FROM counterparties") if repo._pool else 0
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "pending": pending, "approved": approved,
         "rejected": rejected, "docs": docs, "cps": cps},
    )


# ---------------------------------------------------------------------------
# Сырые документы — проверка
# ---------------------------------------------------------------------------

@router.get("/raw", response_class=HTMLResponse)
async def raw_list(request: Request, status: Optional[str] = None):
    repo = get_repo(request)
    items = await repo.list_raw_documents(status=status or "pending")
    pending_count = await repo.count_raw_documents("pending")
    return templates.TemplateResponse(
        "raw_list.html",
        {"request": request, "items": items, "status": status or "pending",
         "pending_count": pending_count, "status_labels": STATUS_LABELS},
    )


@router.get("/raw/{raw_id}", response_class=HTMLResponse)
async def raw_check(request: Request, raw_id: int):
    repo = get_repo(request)
    raw = await repo.get_raw_document(raw_id)
    if not raw:
        return HTMLResponse("<h1>Запись не найдена</h1>", status_code=404)

    data = raw["raw_data"]
    doc_type = data.get("docType", "unknown")
    spec = FORM_SPECS.get(doc_type, FORM_SPECS["unknown"])
    values = get_form_values(data, doc_type)
    items = data.get("items") or []

    return templates.TemplateResponse(
        "raw_check.html",
        {
            "request": request,
            "raw": raw,
            "data": data,
            "doc_type": doc_type,
            "groups": spec,
            "values": values,
            "items": items,
            "raw_json": json.dumps(data, ensure_ascii=False, indent=2),
        },
    )


@router.post("/raw/{raw_id}/save")
async def raw_save(request: Request, raw_id: int, form: Dict[str, str] = Form({})):
    """Сохранить проверенные данные: запись в документы, статус approved."""
    repo = get_repo(request)
    raw = await repo.get_raw_document(raw_id)
    if not raw:
        return HTMLResponse("<h1>Запись не найдена</h1>", status_code=404)

    data = raw["raw_data"]
    doc_type = data.get("docType", "unknown")
    result = build_result_from_form(data, form, doc_type)

    doc_id = await repo.save_document(result)
    await repo.mark_raw_document(raw_id, "approved", processed_doc_id=doc_id)
    print(f"[web] Сырой документ {raw_id} сохранён → documents.id={doc_id}")
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/raw/{raw_id}/reject")
async def raw_reject(request: Request, raw_id: int):
    """Отклонить запись."""
    repo = get_repo(request)
    await repo.mark_raw_document(raw_id, "rejected")
    print(f"[web] Сырой документ {raw_id} отклонён")
    return RedirectResponse("/raw", status_code=303)


# ---------------------------------------------------------------------------
# Документы
# ---------------------------------------------------------------------------

@router.get("/documents", response_class=HTMLResponse)
async def documents_list(request: Request):
    repo = get_repo(request)
    rows = await repo._pool.fetch(
        """
        SELECT d.*, (SELECT count(*) FROM document_items i WHERE i.document_id = d.id) AS items_count
        FROM documents d ORDER BY d.processed_at DESC LIMIT 200
        """
    )
    items = []
    for r in rows:
        d = dict(r)
        d["doc_date"] = fmt_date(d.get("doc_date"))
        d["total_with_vat"] = fmt_num(d.get("total_with_vat"))
        d["total_amount"] = fmt_num(d.get("total_amount"))
        d["amount"] = fmt_num(d.get("amount"))
        d["contract_amount"] = fmt_num(d.get("contract_amount"))
        d["processed_at"] = d.get("processed_at").strftime("%d.%m.%Y %H:%M") if d.get("processed_at") else ""
        items.append(d)
    return templates.TemplateResponse(
        "documents_list.html",
        {"request": request, "items": items},
    )


@router.get("/documents/{doc_id}", response_class=HTMLResponse)
async def document_edit_page(request: Request, doc_id: int):
    repo = get_repo(request)
    row = await repo._pool.fetchrow("SELECT * FROM documents WHERE id = $1", doc_id)
    if not row:
        return HTMLResponse("<h1>Документ не найден</h1>", status_code=404)
    doc = dict(row)

    items = await repo._pool.fetch(
        "SELECT * FROM document_items WHERE document_id = $1 ORDER BY id", doc_id
    )
    doc["items_list"] = [dict(i) for i in items]

    # контрагенты, связанные с документом (через contract_parties и raw-дамп)
    cps = await repo._pool.fetch(
        """
        SELECT c.* FROM contract_parties cp JOIN counterparties c ON c.id = cp.counterparty_id
        WHERE cp.document_id = $1 ORDER BY c.id
        """, doc_id,
    )
    doc["counterparties"] = [dict(c) for c in cps]

    personal = await repo._pool.fetchrow(
        "SELECT * FROM personal_documents WHERE document_id = $1", doc_id
    )
    doc["personal"] = dict(personal) if personal else None

    return templates.TemplateResponse(
        "document_edit.html",
        {"request": request, "doc": doc},
    )


@router.post("/documents/{doc_id}")
async def document_save(request: Request, doc_id: int, form: Dict[str, str] = Form({})):
    """Обновить поля документа."""
    repo = get_repo(request)
    row = await repo._pool.fetchrow("SELECT * FROM documents WHERE id = $1", doc_id)
    if not row:
        return HTMLResponse("<h1>Документ не найден</h1>", status_code=404)

    from repo.postgres import parse_date, to_number, to_text, to_bool

    # Обновляем только поля, которые реально пришли в форме (не пустые).
    # Пустые значения не затирают существующие данные.
    field_map = {
        "doc_number": ("doc_number", to_text),
        "doc_date": ("doc_date", parse_date),
        "ground": ("ground", to_text),
        "payment_purpose": ("payment_purpose", to_text),
        "amount": ("amount", to_number),
        "payment_deadline": ("payment_deadline", parse_date),
        "place": ("place", to_text),
        "subject": ("subject", to_text),
        "contract_amount": ("contract_amount", to_number),
        "term_start": ("term_start", parse_date),
        "term_end": ("term_end", parse_date),
        "payment_terms": ("payment_terms", to_text),
        "key_obligations": ("key_obligations", to_text),
        "contract_ref": ("contract_ref", to_text),
        "period_from": ("period_from", parse_date),
        "period_to": ("period_to", parse_date),
        "total_without_vat": ("total_without_vat", to_number),
        "total_vat": ("total_vat", to_number),
        "total_with_vat": ("total_with_vat", to_number),
        "total_amount": ("total_amount", to_number),
        "dispute_text": ("dispute_text", to_text),
        "letter_subject": ("letter_subject", to_text),
        "summary": ("summary", to_text),
        "full_text": ("full_text", to_text),
        "detected_type": ("detected_type", to_text),
        "extracted_text": ("extracted_text", to_text),
        "notes": ("notes", to_text),
    }

    updates: Dict[str, Any] = {}
    for form_key, (col, converter) in field_map.items():
        raw_val = form.get(form_key, "")
        val = converter(raw_val)
        if val is not None:
            updates[col] = val

    # Чекбоксы: обновляем только если пришли
    if "has_disputes" in form:
        updates["has_disputes"] = form.get("has_disputes") == "on"
    if "requires_action" in form:
        updates["requires_action"] = form.get("requires_action") == "on"

    if updates:
        set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(updates))
        async with repo._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE documents SET {set_clause} WHERE id = ${len(updates) + 1}",
                *updates.values(), doc_id,
            )

    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


@router.post("/documents/{doc_id}/items")
async def document_items_save(request: Request, doc_id: int, form: Dict[str, str] = Form({})):
    """Перезаписать позиции документа."""
    repo = get_repo(request)
    from repo.postgres import to_number, to_text

    async with repo._pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM document_items WHERE document_id = $1", doc_id)
            n_items = int(form.get("n_items") or 0)
            for i in range(n_items):
                name = to_text(form.get(f"item_{i}_name"))
                if not name:
                    continue
                await conn.execute(
                    """
                    INSERT INTO document_items
                        (document_id, name, quantity, unit, price, amount, vat_rate, vat_amount)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    """,
                    doc_id,
                    name,
                    to_number(form.get(f"item_{i}_quantity")),
                    to_text(form.get(f"item_{i}_unit")),
                    to_number(form.get(f"item_{i}_price")),
                    to_number(form.get(f"item_{i}_amount")),
                    to_number(form.get(f"item_{i}_vat_rate")),
                    to_number(form.get(f"item_{i}_vat_amount")),
                )
    return RedirectResponse(f"/documents/{doc_id}", status_code=303)


# ---------------------------------------------------------------------------
# Контрагенты
# ---------------------------------------------------------------------------

COUNTERPARTY_FIELDS = [
    ("name", "Наименование", "text"),
    ("short_name", "Сокращённое наименование", "text"),
    ("inn", "ИНН", "text"),
    ("kpp", "КПП", "text"),
    ("ogrn", "ОГРН", "text"),
    ("address", "Адрес", "text"),
    ("bank", "Банк", "text"),
    ("bik", "БИК", "text"),
    ("account_number", "Расчётный счёт", "text"),
    ("correspondent_account", "Корр. счёт", "text"),
    ("representative", "Представитель", "text"),
]


@router.get("/counterparties", response_class=HTMLResponse)
async def counterparties_list(request: Request, q: str = ""):
    repo = get_repo(request)
    if q:
        rows = await repo._pool.fetch(
            """
            SELECT * FROM counterparties
            WHERE name ILIKE '%' || $1 || '%' OR inn = $1 OR short_name ILIKE '%' || $1 || '%'
            ORDER BY name LIMIT 200
            """, q,
        )
    else:
        rows = await repo._pool.fetch(
            "SELECT * FROM counterparties ORDER BY name LIMIT 200"
        )
    items = [dict(r) for r in rows]
    return templates.TemplateResponse(
        "counterparties_list.html",
        {"request": request, "items": items, "q": q},
    )


@router.get("/counterparties/new", response_class=HTMLResponse)
async def counterparty_new(request: Request):
    return templates.TemplateResponse(
        "counterparty_edit.html",
        {"request": request, "cp": {f: "" for f, _, _ in COUNTERPARTY_FIELDS},
         "fields": COUNTERPARTY_FIELDS, "is_new": True},
    )


@router.get("/counterparties/{cp_id}", response_class=HTMLResponse)
async def counterparty_edit_page(request: Request, cp_id: int):
    repo = get_repo(request)
    row = await repo._pool.fetchrow("SELECT * FROM counterparties WHERE id = $1", cp_id)
    if not row:
        return HTMLResponse("<h1>Контрагент не найден</h1>", status_code=404)
    cp = dict(row)
    return templates.TemplateResponse(
        "counterparty_edit.html",
        {"request": request, "cp": cp, "fields": COUNTERPARTY_FIELDS, "is_new": False},
    )


@router.post("/counterparties/save")
async def counterparty_save(request: Request, form: Dict[str, str] = Form({})):
    """Создать (cp_id пустой) или обновить контрагента."""
    repo = get_repo(request)
    from repo.postgres import to_text

    cp_id = form.get("cp_id") or ""
    values = {f: to_text(form.get(f)) for f, _, _ in COUNTERPARTY_FIELDS}

    async with repo._pool.acquire() as conn:
        if cp_id:
            set_clause = ", ".join(f"{k} = ${i + 1}" for i, k in enumerate(values))
            await conn.execute(
                f"UPDATE counterparties SET {set_clause}, updated_at = now() WHERE id = ${len(values) + 1}",
                *values.values(), int(cp_id),
            )
        else:
            row = await conn.fetchrow(
                """
                INSERT INTO counterparties
                    (name, short_name, inn, kpp, ogrn, address, bank, bik,
                     account_number, correspondent_account, representative)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                RETURNING id
                """,
                *values.values(),
            )
            cp_id = str(row["id"])
    return RedirectResponse(f"/counterparties/{cp_id}", status_code=303)


@router.post("/counterparties/{cp_id}/delete")
async def counterparty_delete(request: Request, cp_id: int):
    repo = get_repo(request)
    async with repo._pool.acquire() as conn:
        await conn.execute("DELETE FROM counterparties WHERE id = $1", cp_id)
    return RedirectResponse("/counterparties", status_code=303)
