"""
Спецификация полей веб-формы проверки документов.

Каждый тип документа — список групп: (заголовок, [(json_ключ, html_имя, тип), ...]).
Типы: text, date, number, boolean, textareas.

Форма предзаполняется из JSON-дампа (raw_data); при сохранении поля
собираются обратно в dict с camelCase-ключами — и передаются в
DocumentRepository.save_document().
"""

from typing import Any, Dict, List, Tuple

# Ключи JSON → группы формы. Пустая строка в json_ключе — поле не из JSON.
FORM_SPECS: Dict[str, List[Tuple[str, List[Tuple[str, str, str]]]]] = {
    "upd": [
        ("Документ", [
            ("docNumber", "docNumber", "text"),
            ("docDate", "docDate", "date"),
        ]),
        ("Поставщик", [
            ("supplier.name", "supplier_name", "text"),
            ("supplier.inn", "supplier_inn", "text"),
            ("supplier.kpp", "supplier_kpp", "text"),
        ]),
        ("Покупатель", [
            ("buyer.name", "buyer_name", "text"),
            ("buyer.inn", "buyer_inn", "text"),
            ("buyer.kpp", "buyer_kpp", "text"),
        ]),
        ("Итоги", [
            ("totalWithoutVat", "totalWithoutVat", "number"),
            ("totalVat", "totalVat", "number"),
            ("totalWithVat", "totalWithVat", "number"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "invoice": [
        ("Документ", [
            ("docNumber", "docNumber", "text"),
            ("docDate", "docDate", "date"),
            ("ground", "ground", "text"),
        ]),
        ("Продавец", [
            ("supplier.name", "supplier_name", "text"),
            ("supplier.inn", "supplier_inn", "text"),
            ("supplier.kpp", "supplier_kpp", "text"),
            ("supplier.address", "supplier_address", "text"),
        ]),
        ("Покупатель", [
            ("buyer.name", "buyer_name", "text"),
            ("buyer.inn", "buyer_inn", "text"),
            ("buyer.kpp", "buyer_kpp", "text"),
            ("buyer.address", "buyer_address", "text"),
        ]),
        ("Итоги", [
            ("totalWithoutVat", "totalWithoutVat", "number"),
            ("totalVat", "totalVat", "number"),
            ("totalWithVat", "totalWithVat", "number"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "payment_order": [
        ("Документ", [
            ("docNumber", "docNumber", "text"),
            ("docDate", "docDate", "date"),
            ("paymentPurpose", "paymentPurpose", "text"),
            ("amount", "amount", "number"),
            ("paymentDeadline", "paymentDeadline", "date"),
        ]),
        ("Продавец", [
            ("seller.name", "seller_name", "text"),
            ("seller.inn", "seller_inn", "text"),
            ("seller.kpp", "seller_kpp", "text"),
            ("seller.bank", "seller_bank", "text"),
            ("seller.bik", "seller_bik", "text"),
            ("seller.accountNumber", "seller_accountNumber", "text"),
            ("seller.correspondentAccount", "seller_correspondentAccount", "text"),
        ]),
        ("Плательщик", [
            ("payer.name", "payer_name", "text"),
            ("payer.inn", "payer_inn", "text"),
            ("payer.kpp", "payer_kpp", "text"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "contract": [
        ("Документ", [
            ("docNumber", "docNumber", "text"),
            ("docDate", "docDate", "date"),
            ("place", "place", "text"),
            ("subject", "subject", "textarea"),
            ("contractAmount", "contractAmount", "number"),
            ("termStart", "termStart", "date"),
            ("termEnd", "termEnd", "date"),
            ("paymentTerms", "paymentTerms", "textarea"),
            ("keyObligations", "keyObligations", "textarea"),
        ]),
        ("Сторона 1", [
            ("party1.name", "party1_name", "text"),
            ("party1.shortName", "party1_shortName", "text"),
            ("party1.inn", "party1_inn", "text"),
            ("party1.kpp", "party1_kpp", "text"),
            ("party1.ogrn", "party1_ogrn", "text"),
            ("party1.representative", "party1_representative", "text"),
            ("party1.role", "party1_role", "text"),
        ]),
        ("Сторона 2", [
            ("party2.name", "party2_name", "text"),
            ("party2.shortName", "party2_shortName", "text"),
            ("party2.inn", "party2_inn", "text"),
            ("party2.kpp", "party2_kpp", "text"),
            ("party2.ogrn", "party2_ogrn", "text"),
            ("party2.representative", "party2_representative", "text"),
            ("party2.role", "party2_role", "text"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "act": [
        ("Документ", [
            ("docNumber", "docNumber", "text"),
            ("docDate", "docDate", "date"),
            ("contractRef", "contractRef", "text"),
            ("periodFrom", "periodFrom", "date"),
            ("periodTo", "periodTo", "date"),
            ("totalAmount", "totalAmount", "number"),
            ("hasDisputes", "hasDisputes", "boolean"),
            ("disputeText", "disputeText", "textarea"),
        ]),
        ("Исполнитель", [
            ("executor.name", "executor_name", "text"),
            ("executor.inn", "executor_inn", "text"),
            ("executor.kpp", "executor_kpp", "text"),
        ]),
        ("Заказчик", [
            ("customer.name", "customer_name", "text"),
            ("customer.inn", "customer_inn", "text"),
            ("customer.kpp", "customer_kpp", "text"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "letter": [
        ("Документ", [
            ("docNumber", "docNumber", "text"),
            ("docDate", "docDate", "date"),
            ("subject", "subject", "text"),
            ("summary", "summary", "textarea"),
            ("fullText", "fullText", "textarea"),
            ("requiresAction", "requiresAction", "boolean"),
        ]),
        ("Отправитель", [
            ("sender.organization", "sender_organization", "text"),
            ("sender.person", "sender_person", "text"),
            ("sender.position", "sender_position", "text"),
        ]),
        ("Получатель", [
            ("recipient.organization", "recipient_organization", "text"),
            ("recipient.person", "recipient_person", "text"),
            ("recipient.position", "recipient_position", "text"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "personal": [
        ("Документ", [
            ("subType", "subType", "text"),
            ("holderName", "holderName", "text"),
            ("docNumber", "docNumber", "text"),
            ("series", "series", "text"),
            ("issuedBy", "issuedBy", "text"),
            ("issueDate", "issueDate", "date"),
            ("expiryDate", "expiryDate", "date"),
        ]),
        ("Персональные данные", [
            ("personalData.fullName", "pd_fullName", "text"),
            ("personalData.dateOfBirth", "pd_dateOfBirth", "date"),
            ("personalData.placeOfBirth", "pd_placeOfBirth", "text"),
            ("personalData.address", "pd_address", "text"),
            ("personalData.gender", "pd_gender", "text"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
    "unknown": [
        ("Документ", [
            ("detectedType", "detectedType", "text"),
            ("summary", "summary", "textarea"),
            ("extractedText", "extractedText", "textarea"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea"),
        ]),
    ],
}

# Ключи, которые не редактируются в форме (заполняются автоматически)
NON_EDITABLE = ("docType", "fileName")


def get_nested(d: Dict[str, Any], dotted_key: str) -> Any:
    """Получить значение по ключу вида 'supplier.name'."""
    cur: Any = d
    for part in dotted_key.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def set_nested(d: Dict[str, Any], dotted_key: str, value: Any) -> None:
    """Установить значение по ключу вида 'supplier.name' (создаёт вложенные dict)."""
    parts = dotted_key.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


def get_form_values(raw: Dict[str, Any], doc_type: str) -> Dict[str, str]:
    """
    Преобразовать JSON-дамп в плоский dict html_имя → строка
    для предзаполнения формы.
    """
    spec = FORM_SPECS.get(doc_type, FORM_SPECS["unknown"])
    values: Dict[str, str] = {}
    for _title, fields in spec:
        for json_key, html_name, _ftype in fields:
            v = get_nested(raw, json_key)
            if v is None:
                values[html_name] = ""
            elif isinstance(v, bool):
                values[html_name] = "on" if v else ""
            else:
                values[html_name] = str(v)
    return values


def build_result_from_form(raw: Dict[str, Any], form: Dict[str, str],
                           doc_type: str) -> Dict[str, Any]:
    """
    Собрать итоговый dict (camelCase-ключи) из исходного дампа
    и значений формы. Позиции (items) берутся из исходного дампа —
    они редактируются отдельно на странице документов.
    """
    result = {k: v for k, v in raw.items() if k not in ("rawResponse", "parseError")}
    spec = FORM_SPECS.get(doc_type, FORM_SPECS["unknown"])

    for _title, fields in spec:
        for json_key, html_name, ftype in fields:
            if json_key in NON_EDITABLE:
                continue
            val = (form.get(html_name) or "").strip()
            if ftype == "boolean":
                if val == "on":
                    set_nested(result, json_key, True)
                else:
                    result.pop(json_key, None)
                    # убрать вложенные ключи, если поле было вложенным
                    if "." in json_key:
                        _pop_parent_key(result, json_key)
            elif ftype == "number":
                if val:
                    try:
                        num = float(val.replace(",", "."))
                        set_nested(result, json_key, int(num) if num == int(num) else num)
                    except ValueError:
                        pass
                else:
                    _remove_key(result, json_key)
            else:  # text / date / textarea
                if val:
                    set_nested(result, json_key, val)
                else:
                    _remove_key(result, json_key)

    # Чистим пустые вложенные dict (например, supplier: {})
    for key in ("supplier", "buyer", "seller", "payer", "party1", "party2",
                "executor", "customer", "sender", "recipient", "personalData"):
        if isinstance(result.get(key), dict) and not result[key]:
            result.pop(key, None)

    return result


def _remove_key(d: Dict[str, Any], dotted_key: str) -> None:
    parts = dotted_key.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            return
        cur = cur[part]
    cur.pop(parts[-1], None)
    # если родитель стал пустым — убрать его тоже
    if "." in dotted_key:
        _pop_parent_key(d, dotted_key)


def _pop_parent_key(d: Dict[str, Any], dotted_key: str) -> None:
    parts = dotted_key.split(".")
    cur = d
    parent = None
    for part in parts[:-1]:
        parent = cur
        if not isinstance(cur.get(part), dict):
            return
        cur = cur[part]
    if parent is not None and isinstance(cur, dict) and not cur:
        parent.pop(parts[-2], None)
