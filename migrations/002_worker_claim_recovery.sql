ALTER TABLE load_events
    ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ;

ALTER TABLE load_actions
    DROP CONSTRAINT IF EXISTS load_actions_status_check;

ALTER TABLE load_actions
    DROP CONSTRAINT IF EXISTS ck_load_actions_status;

ALTER TABLE load_actions
    ADD CONSTRAINT ck_load_actions_status
    CHECK (status IN (
        'queued', 'sent', 'delivered', 'failed', 'completed', 'no_action',
        'simulated'
    ));

CREATE INDEX IF NOT EXISTS idx_load_events_stale_processing
    ON load_events (processing_started_at)
    WHERE processing_status = 'processing';
