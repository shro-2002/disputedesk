-- DisputeDesk core tables. Owner: W1. Idempotent; db.connect() runs this on every connect.
CREATE TABLE IF NOT EXISTS transactions (
    txn_id           TEXT PRIMARY KEY,
    merchant_id      TEXT NOT NULL,
    credential_id    TEXT NOT NULL,
    customer_id      TEXT NOT NULL,
    amount_cents     INTEGER NOT NULL CHECK (amount_cents >= 0),
    txn_date         TEXT NOT NULL,  -- ISO YYYY-MM-DD
    account_id       TEXT,
    ip_address       TEXT,
    device_id        TEXT,
    shipping_address TEXT,
    is_disputed      INTEGER NOT NULL DEFAULT 0 CHECK (is_disputed IN (0, 1)),
    has_fraud_report INTEGER NOT NULL DEFAULT 0 CHECK (has_fraud_report IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_transactions_merchant_id_credential_id
    ON transactions (merchant_id, credential_id);

CREATE TABLE IF NOT EXISTS disputes (
    dispute_id    TEXT PRIMARY KEY,
    case_label    TEXT NOT NULL,
    txn_id        TEXT NOT NULL REFERENCES transactions (txn_id),
    merchant_id   TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    customer_id   TEXT NOT NULL,
    amount_cents  INTEGER NOT NULL CHECK (amount_cents >= 0),
    dispute_date  TEXT NOT NULL,  -- ISO YYYY-MM-DD
    reason_code   TEXT NOT NULL DEFAULT '10.4',
    status        TEXT NOT NULL DEFAULT 'OPEN'
);

CREATE INDEX IF NOT EXISTS idx_disputes_customer_id_dispute_date
    ON disputes (customer_id, dispute_date);
