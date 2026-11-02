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
FORM_SPECS: Dict[str, List[Tuple[str, List[Tuple[str, str, str, str]]]]] = {
    "upd": [
        ("Документ", [
            ("docNumber", "docNumber", "text", "Номер"),
            ("docDate", "docDate", "text", "Дата"),
        ]),
        ("Поставщик", [
            ("supplier.name", "supplier_name", "text", "Наименование"),
            ("supplier.inn", "supplier_inn", "text", "ИНН"),
            ("supplier.kpp", "supplier_kpp", "text", "КПП"),
        ]),
        ("Покупатель", [
            ("buyer.name", "buyer_name", "text", "Наименование"),
            ("buyer.inn", "buyer_inn", "text", "ИНН"),
            ("buyer.kpp", "buyer_kpp", "text", "КПП"),
        ]),
        ("Итоги", [
            ("totalWithoutVat", "totalWithoutVat", "number", "Итого без НДС"),
            ("totalVat", "totalVat", "number", "НДС"),
            ("totalWithVat", "totalWithVat", "number", "Итого с НДС"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "invoice": [
        ("Документ", [
            ("docNumber", "docNumber", "text", "Номер"),
            ("docDate", "docDate", "text", "Дата"),
            ("ground", "ground", "text", "Основание"),
        ]),
        ("Продавец", [
            ("supplier.name", "supplier_name", "text", "Наименование"),
            ("supplier.inn", "supplier_inn", "text", "ИНН"),
            ("supplier.kpp", "supplier_kpp", "text", "КПП"),
            ("supplier.address", "supplier_address", "text", "Адрес"),
        ]),
        ("Покупатель", [
            ("buyer.name", "buyer_name", "text", "Наименование"),
            ("buyer.inn", "buyer_inn", "text", "ИНН"),
            ("buyer.kpp", "buyer_kpp", "text", "КПП"),
            ("buyer.address", "buyer_address", "text", "Адрес"),
        ]),
        ("Итоги", [
            ("totalWithoutVat", "totalWithoutVat", "number", "Итого без НДС"),
            ("totalVat", "totalVat", "number", "НДС"),
            ("totalWithVat", "totalWithVat", "number", "Итого с НДС"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "payment_order": [
        ("Документ", [
            ("docNumber", "docNumber", "text", "Номер"),
            ("docDate", "docDate", "text", "Дата"),
            ("paymentPurpose", "paymentPurpose", "text", "Назначение платежа"),
            ("amount", "amount", "number", "Сумма"),
            ("paymentDeadline", "paymentDeadline", "text", "Срок оплаты"),
        ]),
        ("Продавец", [
            ("seller.name", "seller_name", "text", "Наименование"),
            ("seller.inn", "seller_inn", "text", "ИНН"),
            ("seller.kpp", "seller_kpp", "text", "КПП"),
            ("seller.bank", "seller_bank", "text", "Банк"),
            ("seller.bik", "seller_bik", "text", "БИК"),
            ("seller.accountNumber", "seller_accountNumber", "text", "Р/с"),
            ("seller.correspondentAccount", "seller_correspondentAccount", "text", "Корр. счёт"),
        ]),
        ("Плательщик", [
            ("payer.name", "payer_name", "text", "Наименование"),
            ("payer.inn", "payer_inn", "text", "ИНН"),
            ("payer.kpp", "payer_kpp", "text", "КПП"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "contract": [
        ("Документ", [
            ("docNumber", "docNumber", "text", "Номер"),
            ("docDate", "docDate", "text", "Дата"),
            ("place", "place", "text", "Место заключения"),
            ("subject", "subject", "textarea", "Предмет"),
            ("contractAmount", "contractAmount", "number", "Сумма договора"),
            ("termStart", "termStart", "text", "Начало действия"),
            ("termEnd", "termEnd", "text", "Окончание действия"),
            ("paymentTerms", "paymentTerms", "textarea", "Порядок расчётов"),
            ("keyObligations", "keyObligations", "textarea", "Ключевые обязательства"),
        ]),
        ("Сторона 1", [
            ("party1.name", "party1_name", "text", "Наименование"),
            ("party1.shortName", "party1_shortName", "text", "Сокр. наименование"),
            ("party1.inn", "party1_inn", "text", "ИНН"),
            ("party1.kpp", "party1_kpp", "text", "КПП"),
            ("party1.ogrn", "party1_ogrn", "text", "ОГРН"),
            ("party1.representative", "party1_representative", "text", "Представитель"),
            ("party1.role", "party1_role", "text", "Роль"),
        ]),
        ("Сторона 2", [
            ("party2.name", "party2_name", "text", "Наименование"),
            ("party2.shortName", "party2_shortName", "text", "Сокр. наименование"),
            ("party2.inn", "party2_inn", "text", "ИНН"),
            ("party2.kpp", "party2_kpp", "text", "КПП"),
            ("party2.ogrn", "party2_ogrn", "text", "ОГРН"),
            ("party2.representative", "party2_representative", "text", "Представитель"),
            ("party2.role", "party2_role", "text", "Роль"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "act": [
        ("Документ", [
            ("docNumber", "docNumber", "text", "Номер"),
            ("docDate", "docDate", "text", "Дата"),
            ("contractRef", "contractRef", "text", "Основание (договор)"),
            ("periodFrom", "periodFrom", "text", "Период с"),
            ("periodTo", "periodTo", "text", "Период по"),
            ("totalAmount", "totalAmount", "number", "Итого"),
            ("hasDisputes", "hasDisputes", "boolean", "Есть претензии"),
            ("disputeText", "disputeText", "textarea", "Претензии"),
        ]),
        ("Исполнитель", [
            ("executor.name", "executor_name", "text", "Наименование"),
            ("executor.inn", "executor_inn", "text", "ИНН"),
            ("executor.kpp", "executor_kpp", "text", "КПП"),
        ]),
        ("Заказчик", [
            ("customer.name", "customer_name", "text", "Наименование"),
            ("customer.inn", "customer_inn", "text", "ИНН"),
            ("customer.kpp", "customer_kpp", "text", "КПП"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "letter": [
        ("Документ", [
            ("docNumber", "docNumber", "text", "Номер"),
            ("docDate", "docDate", "text", "Дата"),
            ("subject", "subject", "text", "Предмет"),
            ("summary", "summary", "textarea", "Краткое содержание"),
            ("fullText", "fullText", "textarea", "Полный текст"),
            ("requiresAction", "requiresAction", "boolean", "Требует действий"),
        ]),
        ("Отправитель", [
            ("sender.organization", "sender_organization", "text", "Организация"),
            ("sender.person", "sender_person", "text", "Лицо"),
            ("sender.position", "sender_position", "text", "Должность"),
        ]),
        ("Получатель", [
            ("recipient.organization", "recipient_organization", "text", "Организация"),
            ("recipient.person", "recipient_person", "text", "Лицо"),
            ("recipient.position", "recipient_position", "text", "Должность"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "personal": [
        ("Документ", [
            ("subType", "subType", "text", "Подтип"),
            ("holderName", "holderName", "text", "ФИО владельца"),
            ("docNumber", "docNumber", "text", "Номер"),
            ("series", "series", "text", "Серия"),
            ("issuedBy", "issuedBy", "text", "Кем выдан"),
            ("issueDate", "issueDate", "text", "Дата выдачи"),
            ("expiryDate", "expiryDate", "text", "Действует до"),
        ]),
        ("Персональные данные", [
            ("personalData.fullName", "pd_fullName", "text", "ФИО"),
            ("personalData.dateOfBirth", "pd_dateOfBirth", "text", "Дата рождения"),
            ("personalData.placeOfBirth", "pd_placeOfBirth", "text", "Место рождения"),
            ("personalData.address", "pd_address", "text", "Адрес"),
            ("personalData.gender", "pd_gender", "text", "Пол"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
        ]),
    ],
    "unknown": [
        ("Документ", [
            ("detectedType", "detectedType", "text", "Предполагаемый тип"),
            ("summary", "summary", "textarea", "Краткое содержание"),
            ("extractedText", "extractedText", "textarea", "Извлечённый текст"),
        ]),
        ("Прочее", [
            ("notes", "notes", "textarea", "Примечания"),
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
        for json_key, html_name, _ftype, _label in fields:
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
        for json_key, html_name, ftype, _label in fields:
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
