# Internal Integration Contracts

## Overview

These contracts define the minimal interfaces between the FreightOps agent and its operational dependencies. They are intentionally small, mockable, and easy to implement with HTTP or queue-based adapters.

## 1. LLM SOP decision proposal

The agent calls one configured LLM provider through an adapter only for decision-relevant events. The API key is loaded from secret configuration and is never included in the request context, logs, or persisted decision records. Provider-specific response formats are normalized to this internal contract.

### Request

```json
{
  "schema_version": "1",
  "max_output_tokens": 2048,
  "event": {
    "event_id": "evt_12345",
    "event_type": "message",
    "source_channel": "sms",
    "source_contact_id": "drv_20931",
    "payload": {"text": "What's the PO number?"},
    "occurred_at": "2026-10-13T07:12:00-07:00"
  },
  "load_context": {
    "load_id": "481207",
    "current_stage": "at_pickup",
    "available_facts": {"po_number": "PO-88421"},
    "open_requests": [{"request_id": "req_eta_1", "request_type": "eta_request", "state": "open"}],
    "recent_events": []
  },
  "active_sop": {
    "document_id": "sop_client_a",
    "version": "2026-10-01",
    "content_hash": "sha256:...",
    "sections": [{"section_ref": "pickup.delay", "text": "When pickup delay risk is high, request an ETA..."}]
  },
  "allowed_action_types": ["sms_send", "email_send", "hero_task", "timer_set"]
}
```

`max_output_tokens` is a provider-adapter generation limit, not policy text. The
adapter must enforce it in the provider request and must not include the field
in the model's SOP/event prompt. Input size is preflighted against the configured
token cap; per-load calls and conservative cost reservations are persisted and
bounded before a provider request is made.

### Normalized response

```json
{
  "schema_version": "1",
  "model_disposition": "propose_action",
  "load_id": "481207",
  "sop_document_id": "sop_client_a",
  "sop_version": "2026-10-01",
  "sop_content_hash": "sha256:...",
  "sop_section_ref": "pickup.delay",
  "supporting_passage": "When pickup delay risk is high, request an ETA...",
  "load_evidence": [{"fact_id": "po_number", "value": "PO-88421"}],
  "reason_code": "answers_load_metadata_not_eta",
  "proposed_actions": [
    {"action_type": "sms_send", "contact_id": "drv_20931", "payload": {"message": "The PO number is ..."}}
  ],
  "missing_facts": [],
  "uncertainty": "low"
}
```

`model_disposition` is `propose_action`, `no_action`, or `abstain`. `proposed_actions` must be empty for `no_action` and `abstain`. The application records a separate system decision: unclear, unrelated, or non-applicable events MUST create a `no_action` DecisionRecord; if the model abstains, record `model_disposition=abstain` and a separate `review_status=required`/reason linked to that no-action record. Review status never authorizes an external action. Provider/system failure is separate from a model disposition and is recorded explicitly; it must not be mislabeled as a successful no-action unless the event itself is known to be unclear, unrelated, or non-applicable. The model must not return executable code, select a provider/tool, or propose action types outside the supplied catalog. Deterministic validation checks the schema, active SOP reference/hash and exact passage, precedence, action payload, cited load facts, open-request semantics, contact/channel scope, and current load state. These mechanical checks do not prove the proposal is a semantically correct interpretation of policy, so scenario evaluations and review of uncertain/high-impact decisions remain required. Only accepted proposals may reach action adapters. Abstentions route to human review or a request for missing facts. Invalid output, timeout, or provider error must be surfaced and recorded as rejected/provider-failed; none may authorize an external action. Retry only transient failures; retry count and per-load inference budget are bounded.

Inbound messages, attachments, and metadata are untrusted data, not policy instructions; the model must use them only as evidence and follow only the supplied active SOP. Include factual values such as the PO number only when needed to resolve the current event, send only the minimum load context required, and verify the configured provider's data-handling terms before production use.

## 2. Message ingestion

### `POST /v1/events/ingest`

Request body:
```json
{
  "broker_id": "broker_a",
  "load_id": "481207",
  "event_type": "message",
  "source_channel": "sms",
  "source_contact_id": "drv_20931",
  "payload": {
    "text": "What is the PO number?"
  },
  "occurred_at": "2026-10-13T07:12:00-07:00"
}
```

Response:
```json
{
  "accepted": true,
  "event_id": "evt_12345",
  "status": "queued_for_processing"
}
```

## 3. SMS send

### `POST /v1/notifications/sms/send`

Request body:
```json
{
  "contact_id": "drv_20931",
  "message": "What time will you arrive at the pickup?"
}
```

Response:
```json
{
  "accepted": true,
  "message_id": "sms_987",
  "status": "sent"
}
```

## 4. Email send

### `POST /v1/notifications/email/send`

Request body:
```json
{
  "thread_id": "load_481207_main",
  "to": ["dispatcher@prairiehaul.com"],
  "subject": "ETA needed for load 481207",
  "body": "Your driver has not responded to the ETA request. Please provide an updated ETA."
}
```

Response:
```json
{
  "accepted": true,
  "email_id": "email_654",
  "status": "queued"
}
```

## 5. Slack alert

### `POST /v1/notifications/slack/send`

Request body:
```json
{
  "channel": "broker-alerts",
  "message": "Load 481207 is at high delay risk and requires broker review."
}
```

Response:
```json
{
  "accepted": true,
  "slack_message_id": "slack_321",
  "status": "sent"
}
```

## 6. TMS update

### `POST /v1/tms/status/update`

Request body:
```json
{
  "load_id": "481207",
  "status": "delay_risk_high",
  "note": "Driver ETA request sent and awaiting response."
}
```

Response:
```json
{
  "accepted": true,
  "updated": true,
  "status": "acknowledged"
}
```

## 7. Timer creation

### `POST /v1/timers/create`

Request body:
```json
{
  "load_id": "481207",
  "timer_name": "eta_followup_1",
  "trigger_at": "2026-10-13T07:31:00-07:00",
  "context": {
    "request_id": "req_eta_1"
  }
}
```

Response:
```json
{
  "accepted": true,
  "timer_id": "timer_12",
  "status": "scheduled"
}
```

## 8. Hero task creation

### `POST /v1/tasks/create`

Request body:
```json
{
  "load_id": "481207",
  "priority": "urgent",
  "task_type": "driver_callback",
  "summary": "Contact driver to obtain ETA for load 481207."
}
```

Response:
```json
{
  "accepted": true,
  "task_id": "task_445",
  "status": "created"
}
```

## Contract principles

- All adapters MUST be mockable and return a machine-readable acknowledgement.
- Errors MUST be explicit and must not silently disappear.
- The agent MUST persist the outbound action and correlation identifiers for later auditing.
- Any message or task returned by the adapter must be traceable back to the originating load event and SOP rule.
