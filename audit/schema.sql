-- Append-only audit trail (PRD §6). One row per state transition in the
-- event -> decision -> action -> outcome lifecycle. Metrics (PRD §5) are
-- computed from this table only; it is the single source of truth.

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts              TIMESTAMPTZ  NOT NULL DEFAULT now(),
    event_id        TEXT         NOT NULL,
    customer_id     TEXT         NOT NULL,
    entry_type      TEXT         NOT NULL
        CHECK (entry_type IN (
            'event_ingested',
            'decision',
            'action',
            'outcome',
            'stopping_rule_triggered',
            'manual_status'
        )),
    failure_type    TEXT
        CHECK (failure_type IS NULL OR failure_type IN (
            'payment_retry', 'checkout_abandonment', 'mandate_failure'
        )),
    intervention    TEXT
        CHECK (intervention IS NULL OR intervention IN (
            'voice', 'sms', 'link_only', 'none'
        )),
    reason          TEXT,
    amount_inr      INTEGER,
    attempt_number  INTEGER,
    payload         JSONB,
    -- A logged reason is mandatory for decisions and stopping-rule hits
    -- (CLAUDE.md hard rule: no intervention without a logged reason).
    CONSTRAINT reason_required_for_decisions CHECK (
        entry_type NOT IN ('decision', 'stopping_rule_triggered')
        OR (reason IS NOT NULL AND length(trim(reason)) > 0)
    )
);

-- Migration: widen entry_type to include 'manual_status' on a table that
-- already existed before this value was added (CREATE TABLE IF NOT EXISTS
-- above is a no-op once the table exists, so the original anonymous CHECK
-- constraint would otherwise stick around and reject the new value).
DO $$
DECLARE
    cname text;
BEGIN
    SELECT con.conname INTO cname
    FROM pg_constraint con
    JOIN pg_class rel ON rel.oid = con.conrelid
    WHERE rel.relname = 'audit_log' AND con.contype = 'c'
      AND pg_get_constraintdef(con.oid) LIKE '%entry_type%';
    IF cname IS NOT NULL THEN
        EXECUTE format('ALTER TABLE audit_log DROP CONSTRAINT %I', cname);
    END IF;
    ALTER TABLE audit_log ADD CONSTRAINT audit_log_entry_type_check
        CHECK (entry_type IN (
            'event_ingested', 'decision', 'action', 'outcome',
            'stopping_rule_triggered', 'manual_status'
        ));
END $$;

CREATE INDEX IF NOT EXISTS idx_audit_log_event_id    ON audit_log (event_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_customer_id ON audit_log (customer_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_entry_type  ON audit_log (entry_type);

-- Enforce append-only at the database level: no UPDATE, no DELETE, ever.
CREATE OR REPLACE FUNCTION audit_log_reject_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_audit_log_no_update ON audit_log;
CREATE TRIGGER trg_audit_log_no_update
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation();
