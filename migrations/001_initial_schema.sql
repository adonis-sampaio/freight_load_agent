CREATE TABLE IF NOT EXISTS loads (
    load_id TEXT NOT NULL,
    broker_id TEXT NOT NULL,
    broker_name TEXT NOT NULL,
    shipper_name TEXT,
    carrier_name TEXT,
    carrier_mc_number TEXT,
    commodity TEXT,
    load_type TEXT,
    current_stage TEXT NOT NULL CHECK (
        current_stage IN (
            'dispatched',
            'to_pickup',
            'at_pickup',
            'to_delivery',
            'at_delivery',
            'delivered'
        )
    ),
    delay_risk TEXT NOT NULL DEFAULT 'low'
        CHECK (delay_risk IN ('low', 'medium', 'high')),
    available_facts_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(available_facts_json) = 'object'),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'held', 'resolved', 'exception')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_id, load_id)
);

CREATE TABLE IF NOT EXISTS stops (
    stop_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    type TEXT NOT NULL CHECK (type IN ('pickup', 'delivery')),
    name TEXT,
    address TEXT,
    timezone TEXT,
    appointment_type TEXT CHECK (appointment_type IN ('window', 'fixed')),
    appointment_start TIMESTAMPTZ,
    appointment_end TIMESTAMPTZ,
    appointment_time TIMESTAMPTZ,
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id),
    UNIQUE (broker_id, load_id, sequence),
    CHECK (
        appointment_start IS NULL
        OR appointment_end IS NULL
        OR appointment_start <= appointment_end
    )
);

CREATE TABLE IF NOT EXISTS contacts (
    contact_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    role TEXT NOT NULL
        CHECK (role IN ('driver', 'dispatcher', 'broker', 'hero', 'carrier')),
    channels JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(channels) = 'array'),
    external_ref TEXT NOT NULL,
    primary_channels JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(primary_channels) = 'object'),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id),
    UNIQUE (contact_id, broker_id, load_id)
);

CREATE TABLE IF NOT EXISTS load_events (
    event_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN (
            'message',
            'ping',
            'timer',
            'attachment',
            'metadata_update',
            'stage_change'
        )
    ),
    source_channel TEXT NOT NULL CHECK (
        source_channel IN ('sms', 'email', 'slack', 'internal_timer', 'system')
    ),
    source_contact_id TEXT,
    payload_json JSONB NOT NULL,
    processing_status TEXT NOT NULL DEFAULT 'queued'
        CHECK (processing_status IN ('queued', 'processing', 'completed', 'failed')),
    occurred_at TIMESTAMPTZ NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    correlation_id TEXT,
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id),
    FOREIGN KEY (source_contact_id, broker_id, load_id)
        REFERENCES contacts (contact_id, broker_id, load_id),
    UNIQUE (event_id, broker_id, load_id)
);

CREATE TABLE IF NOT EXISTS sop_documents (
    document_id TEXT PRIMARY KEY,
    client_id TEXT,
    chapter_name TEXT NOT NULL,
    version TEXT NOT NULL,
    content_markdown TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, chapter_name, version)
);

CREATE TABLE IF NOT EXISTS sop_rules (
    rule_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES sop_documents (document_id),
    client_id TEXT,
    load_type TEXT,
    scope TEXT NOT NULL
        CHECK (scope IN ('standard', 'client_override', 'load_type_override')),
    load_stage TEXT CHECK (
        load_stage IS NULL
        OR load_stage IN (
            'dispatched',
            'to_pickup',
            'at_pickup',
            'to_delivery',
            'at_delivery',
            'delivered'
        )
    ),
    section_ref TEXT NOT NULL,
    applicable_event_types JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(applicable_event_types) = 'array'),
    priority INTEGER NOT NULL DEFAULT 0,
    version TEXT NOT NULL,
    effective_from TIMESTAMPTZ NOT NULL,
    effective_to TIMESTAMPTZ,
    CHECK (effective_to IS NULL OR effective_from < effective_to),
    CHECK (
        (scope = 'standard' AND client_id IS NULL)
        OR (scope = 'client_override' AND client_id IS NOT NULL)
        OR (scope = 'load_type_override' AND load_type IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sop_documents_standard_version
    ON sop_documents (chapter_name, version) WHERE client_id IS NULL;

CREATE TABLE IF NOT EXISTS open_requests (
    request_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    request_type TEXT NOT NULL
        CHECK (request_type IN ('eta_request', 'temperature_request', 'seal_request', 'custom_question')),
    question_text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open'
        CHECK (state IN ('open', 'answered', 'expired', 'cancelled')),
    reminder_count INTEGER NOT NULL DEFAULT 0 CHECK (reminder_count >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ,
    resolved_at TIMESTAMPTZ,
    source_contact_id TEXT,
    last_prompted_at TIMESTAMPTZ,
    correlation_id TEXT,
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id),
    FOREIGN KEY (source_contact_id, broker_id, load_id)
        REFERENCES contacts (contact_id, broker_id, load_id)
);

CREATE TABLE IF NOT EXISTS load_actions (
    action_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    action_type TEXT NOT NULL CHECK (
        action_type IN (
            'sms_send',
            'email_send',
            'slack_alert',
            'tms_update',
            'hero_task',
            'timer_set',
            'status_update'
        )
    ),
    status TEXT NOT NULL
        CHECK (status IN ('queued', 'sent', 'delivered', 'failed', 'completed', 'no_action')),
    trigger_event_id TEXT NOT NULL,
    sop_rule_id TEXT REFERENCES sop_rules (rule_id),
    actor TEXT NOT NULL
        CHECK (actor IN ('agent', 'hero', 'broker', 'internal_system')),
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(payload_json) = 'object'),
    correlation_id TEXT,
    external_id TEXT,
    failure_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id),
    FOREIGN KEY (trigger_event_id, broker_id, load_id)
        REFERENCES load_events (event_id, broker_id, load_id)
);

CREATE TABLE IF NOT EXISTS decision_records (
    decision_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    trigger_event_id TEXT NOT NULL,
    sop_rule_id TEXT REFERENCES sop_rules (rule_id),
    disposition TEXT CHECK (disposition IN ('propose_action', 'no_action')),
    model_disposition TEXT
        CHECK (model_disposition IN ('propose_action', 'no_action', 'abstain')),
    review_status TEXT NOT NULL DEFAULT 'not_required'
        CHECK (review_status IN ('not_required', 'required', 'resolved')),
    review_reason TEXT,
    sop_document_id TEXT REFERENCES sop_documents (document_id),
    sop_version TEXT,
    sop_content_hash TEXT,
    ruleset_version TEXT,
    sop_section_ref TEXT,
    supporting_passage TEXT,
    load_evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(load_evidence_refs) = 'array'),
    proposed_actions JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(proposed_actions) = 'array'),
    provider_id TEXT,
    model_id TEXT,
    provider_request_id TEXT,
    prompt_schema_version TEXT,
    uncertainty_level TEXT
        CHECK (uncertainty_level IS NULL OR uncertainty_level IN ('low', 'medium', 'high')),
    validation_status TEXT NOT NULL
        CHECK (validation_status IN (
            'accepted', 'rejected', 'provider_failed', 'budget_exceeded'
        )),
    validation_reasons JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(validation_reasons) = 'array'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id),
    FOREIGN KEY (trigger_event_id, broker_id, load_id)
        REFERENCES load_events (event_id, broker_id, load_id),
    CHECK (
        (disposition IS NOT NULL)
        OR validation_status = 'provider_failed'
    ),
    CHECK (
        disposition <> 'no_action'
        OR jsonb_array_length(proposed_actions) = 0
        OR (
            model_disposition = 'propose_action'
            AND validation_status = 'rejected'
        )
    ),
    CHECK (
        model_disposition <> 'abstain'
        OR (review_status = 'required' AND disposition = 'no_action')
    ),
    CHECK (
        validation_status <> 'accepted'
        OR disposition IS NOT NULL
    )
);

CREATE TABLE IF NOT EXISTS llm_usage_budgets (
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    call_count INTEGER NOT NULL DEFAULT 0 CHECK (call_count >= 0),
    reserved_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0
        CHECK (reserved_cost_usd >= 0),
    estimated_input_tokens BIGINT NOT NULL DEFAULT 0
        CHECK (estimated_input_tokens >= 0),
    reserved_output_tokens BIGINT NOT NULL DEFAULT 0
        CHECK (reserved_output_tokens >= 0),
    last_latency_ms BIGINT,
    total_latency_ms BIGINT NOT NULL DEFAULT 0 CHECK (total_latency_ms >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (broker_id, load_id),
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id)
);

CREATE TABLE IF NOT EXISTS document_attachments (
    document_id TEXT PRIMARY KEY,
    broker_id TEXT NOT NULL,
    load_id TEXT NOT NULL,
    document_type TEXT NOT NULL
        CHECK (document_type IN ('bol', 'pod', 'rate_confirmation', 'receipt', 'unknown')),
    source_uri TEXT NOT NULL,
    content_type TEXT NOT NULL,
    hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received'
        CHECK (status IN ('received', 'classified', 'extracted', 'rejected')),
    extracted_values_json JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(extracted_values_json) = 'object'),
    extraction_version TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (broker_id, load_id) REFERENCES loads (broker_id, load_id)
);

CREATE INDEX IF NOT EXISTS idx_load_events_load_time
    ON load_events (broker_id, load_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_load_events_queued
    ON load_events (ingested_at) WHERE processing_status = 'queued';
CREATE INDEX IF NOT EXISTS idx_load_events_correlation
    ON load_events (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_open_requests_active
    ON open_requests (broker_id, load_id, state, expires_at);
CREATE INDEX IF NOT EXISTS idx_load_actions_load_time
    ON load_actions (broker_id, load_id, created_at);
CREATE INDEX IF NOT EXISTS idx_decision_records_load_time
    ON decision_records (broker_id, load_id, created_at);
CREATE INDEX IF NOT EXISTS idx_sop_rules_active_lookup
    ON sop_rules (client_id, load_type, load_stage, effective_from, effective_to);
