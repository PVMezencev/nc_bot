-- ============================================================================
-- Схema БД для DocumentBot (PostgreSQL >= 15)
--
-- Структура таблиц соответствует JSON-дампу, который бот получает
-- из llama.cpp при обработке сканированных документов (см. bots/document.py).
--
-- Таблицы:
--   raw_documents      — «сырые» JSON-дампы от бота, ожидающие проверки пользователем
--   documents          — документы (по одному на обработанный файл)
--   counterparties     — контрагенты (поставщик/покупатель/продавец/плательщик/
--                        сторона договора/исполнитель/заказчик/отправитель/получатель)
--   document_items     — позиции документов (товары/работы/услуги)
--   contract_parties   — связи «сторона договора — документ»
--   personal_documents — данные личных документов (паспорт, ОМС, СНИЛС и т.д.)
--
-- Контрагенты не дублируются: уникальность по (INN, KPP) при наличии ИНН,
-- иначе по (name, INN). См. upsert в repo/postgres.py.
-- ============================================================================

CREATE TABLE IF NOT EXISTS documents (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- docType: upd | invoice | payment_order | contract | act | letter | personal | unknown
    doc_type            TEXT NOT NULL,
    -- fileName
    file_name           TEXT NOT NULL,
    -- docNumber
    doc_number          TEXT,
    -- docDate (DD.MM.YYYY)
    doc_date            DATE,
    -- ground (счёт-фактура: основание)
    ground              TEXT,
    -- paymentPurpose (счёт на оплату)
    payment_purpose     TEXT,
    -- amount (счёт на оплату: сумма к оплате)
    amount              NUMERIC(20, 2),
    -- paymentDeadline
    payment_deadline    DATE,
    -- place (договор: место заключения)
    place               TEXT,
    -- subject (договор: предмет)
    subject             TEXT,
    -- contractAmount (договор)
    contract_amount     NUMERIC(20, 2),
    -- termStart / termEnd (договор)
    term_start          DATE,
    term_end            DATE,
    -- paymentTerms (договор)
    payment_terms       TEXT,
    -- keyObligations (договор)
    key_obligations     TEXT,
    -- contractRef (акт: ссылка на договор)
    contract_ref        TEXT,
    -- periodFrom / periodTo (акт)
    period_from         DATE,
    period_to           DATE,
    -- totalWithoutVat / totalVat / totalWithVat (УПД, счёт-фактура)
    total_without_vat   NUMERIC(20, 2),
    total_vat           NUMERIC(20, 2),
    total_with_vat      NUMERIC(20, 2),
    -- totalAmount (акт)
    total_amount        NUMERIC(20, 2),
    -- hasDisputes / disputeText (акт)
    has_disputes        BOOLEAN,
    dispute_text        TEXT,
    -- subject / summary / requiresAction (письмо)
    letter_subject      TEXT,
    summary             TEXT,
    full_text           TEXT,
    requires_action     BOOLEAN,
    -- detectedType / extractedText (неопределённый тип)
    detected_type       TEXT,
    extracted_text      TEXT,
    -- keyFields (неопределённый тип)
    key_fields          JSONB,
    -- notes
    notes               TEXT,
    -- служебные
    processed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (file_name, doc_type, doc_number)
);

CREATE INDEX IF NOT EXISTS idx_documents_doc_type ON documents (doc_type);
CREATE INDEX IF NOT EXISTS idx_documents_doc_date ON documents (doc_date);

-- ----------------------------------------------------------------------------
-- «Сырые» данные от бота — ждут проверки пользователем через веб-интерфейс.
-- status: pending (не проверена) | approved (сохранена) | rejected (отклонена)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS raw_documents (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- docType из дампа
    doc_type            TEXT NOT NULL,
    -- fileName из дампа
    file_name           TEXT NOT NULL,
    -- полный JSON-дамп от бота
    raw_data            JSONB NOT NULL,
    -- pending | approved | rejected
    status              TEXT NOT NULL DEFAULT 'pending',
    -- id строки documents, созданной при сохранении (для approved)
    processed_doc_id    BIGINT REFERENCES documents (id) ON DELETE SET NULL,
    -- служебные
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_raw_documents_status ON raw_documents (status);
CREATE INDEX IF NOT EXISTS idx_raw_documents_created ON raw_documents (created_at);

-- ----------------------------------------------------------------------------
-- Контрагенты (организации/лица, встречающиеся в документах)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS counterparties (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- name (наименование)
    name                TEXT,
    -- shortName (договор)
    short_name          TEXT,
    -- inn
    inn                 TEXT,
    -- kpp
    kpp                 TEXT,
    -- ogrn (договор)
    ogrn                TEXT,
    -- address (счёт-фактура)
    address             TEXT,
    -- bank / bik / accountNumber / correspondentAccount (счёт на оплату)
    bank                TEXT,
    bik                 TEXT,
    account_number      TEXT,
    correspondent_account TEXT,
    -- representative (договор: представитель и основание)
    representative      TEXT,
    -- служебные
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Уникальность: контрагент с ИНН уникален по (INN, KPP);
-- без ИНН — по (name, INN). NULL в UNIQUE не конфликтует,
-- поэтому для записей без ИНН уникальность обеспечивает name.
CREATE UNIQUE INDEX IF NOT EXISTS ux_counterparties_inn_kpp
    ON counterparties (inn, kpp) WHERE inn IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_counterparties_name_inn
    ON counterparties (lower(name), inn) WHERE inn IS NULL AND name IS NOT NULL;

-- ----------------------------------------------------------------------------
-- Позиции документов (товары / работы / услуги)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS document_items (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id         BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    -- name (УПД, счёт-фактура) / description (акт)
    name                TEXT,
    -- quantity
    quantity            NUMERIC(20, 4),
    -- unit
    unit                TEXT,
    -- price
    price               NUMERIC(20, 2),
    -- amount
    amount              NUMERIC(20, 2),
    -- vatRate / vatAmount (УПД, счёт-фактура)
    vat_rate            NUMERIC(5, 2),
    vat_amount          NUMERIC(20, 2),
    -- служебные
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_document_items_document ON document_items (document_id);

-- ----------------------------------------------------------------------------
-- Связь «сторона договора — документ» (party1 / party2)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS contract_parties (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id         BIGINT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    counterparty_id     BIGINT NOT NULL REFERENCES counterparties (id) ON DELETE CASCADE,
    -- role: заказчик | исполнитель | покупатель | поставщик
    role                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, counterparty_id, role)
);

-- ----------------------------------------------------------------------------
-- Личные документы (паспорт, полис ОМС, СНИЛС, права, сертификат)
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS personal_documents (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id         BIGINT NOT NULL UNIQUE REFERENCES documents (id) ON DELETE CASCADE,
    -- subType: passport | oms_policy | snils | driver_license | certificate | other
    sub_type            TEXT,
    -- holderName
    holder_name         TEXT,
    -- series
    series              TEXT,
    -- issuedBy
    issued_by           TEXT,
    -- issueDate
    issue_date          DATE,
    -- expiryDate
    expiry_date         DATE,
    -- personalData.*
    full_name           TEXT,
    date_of_birth       DATE,
    place_of_birth      TEXT,
    address             TEXT,
    gender              TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
