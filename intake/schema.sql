-- Merchant intake: an uploaded sheet becomes a `batch`, one `batch_row`
-- per contact. The recovery run for each row writes to audit_log tagged
-- with the batch_id (payload->>'batch_id'), so the metrics view can
-- slice by batch. batch_row carries the run status for the live table.

CREATE TABLE IF NOT EXISTS batch (
    id              TEXT PRIMARY KEY,          -- short random id
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    name            TEXT NOT NULL,
    merchant        TEXT NOT NULL,
    source_filename TEXT,
    -- consent (PRD 1a): the uploader attests the numbers are the
    -- merchant's own customers with a transaction relationship + consent.
    attested        BOOLEAN NOT NULL DEFAULT FALSE,
    attested_text   TEXT,
    status          TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'failed')),
    total_rows      INTEGER NOT NULL DEFAULT 0,
    config          JSONB
);

CREATE TABLE IF NOT EXISTS batch_row (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_id        TEXT NOT NULL REFERENCES batch(id) ON DELETE CASCADE,
    row_index       INTEGER NOT NULL,
    event_id        TEXT NOT NULL,             -- correlates to audit_log
    customer_name   TEXT,
    phone           TEXT,
    amount_inr      INTEGER,
    failure_type    TEXT,
    reference_id    TEXT,
    error_code      TEXT,
    timezone        TEXT DEFAULT 'Asia/Kolkata',
    raw             JSONB,                     -- original sheet row
    status          TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','in_progress','completed','failed','blocked','skipped')),
    decided_intervention TEXT,
    result          TEXT,
    error           TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_batch_row_batch    ON batch_row (batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_row_status   ON batch_row (batch_id, status);
CREATE INDEX IF NOT EXISTS idx_batch_created      ON batch (created_at DESC);
