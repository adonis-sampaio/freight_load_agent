# Data Model: Freight Load SOP Monitoring Agent

## Overview

The system models each load as a stateful operational record with a timeline of events, open requests, and actions. The primary design goal is to keep every decision explainable and auditable while remaining efficient at scale.

## Core Entities

### Load

Represents an active shipment or movement.

Fields:
- `load_id`: unique identifier
- `broker_id`: client identifier
- `broker_name`: human-readable broker name
- `shipper_name`: shipper or origin reference
- `carrier_name`: carrier name
- `carrier_mc_number`: carrier MC number
- `commodity`: commodity description
- `current_stage`: dispatched, to_pickup, at_pickup, to_delivery, at_delivery, delivered
- `delay_risk`: low, medium, high
- `available_facts`: operational metadata needed for SOP decisions, such as PO number or appointment details
- `status`: active, held, resolved, exception
- `created_at`: timestamp
- `updated_at`: timestamp

Relationships:
- one-to-many with `Stop`
- one-to-many with `LoadEvent`
- one-to-many with `LoadAction`
- one-to-many with `OpenRequest`
- one-to-many with `DocumentAttachment`

Validation rules:
- `load_id` must be unique within the broker context.
- `current_stage` must be one of the defined lifecycle states.
- `delay_risk` must be recalculated or updated after each location ping.

### Stop

Represents a pickup or delivery stop with appointment details.

Fields:
- `stop_id`
- `load_id`
- `sequence`
- `type`: pickup or delivery
- `name`
- `address`
- `timezone`
- `appointment_type`: window or fixed
- `appointment_start`
- `appointment_end`
- `appointment_time`

Relationships:
- many-to-one with `Load`

Validation rules:
- `sequence` must be unique per load.
- pickup and delivery stops must maintain a valid order along the route.

### Contact

Represents a person or destination that can receive communication or be referenced in the load record.

Fields:
- `contact_id`
- `load_id`
- `role`: driver, dispatcher, broker, hero, carrier
- `channels`: sms, email, slack
- `external_ref`: phone number, email address, or system id
- `is_active`

Relationships:
- many-to-one with `Load`
- one-to-many with `LoadAction`

Validation rules:
- `contact_id` must map to a valid external address in the message service layer.
- A driver may have multiple channels, but only one primary operational channel for a given step.

### LoadEvent

Represents a single observed system input.

Fields:
- `event_id`
- `load_id`
- `event_type`: message, ping, timer, attachment, metadata_update, stage_change
- `source_channel`: sms, email, slack, internal_timer, system
- `source_contact_id`
- `payload_json`
- `occurred_at`
- `ingested_at`

Relationships:
- many-to-one with `Load`

Validation rules:
- `payload_json` must be parseable for known event types.
- timer events must correlate to an existing open request or scheduled action.

### OpenRequest

Tracks unresolved questions that require an answer from a driver, broker, or dispatcher.

Fields:
- `request_id`
- `load_id`
- `request_type`: eta_request, temperature_request, seal_request, custom_question
- `question_text`
- `state`: open, answered, expired, cancelled
- `created_at`
- `expires_at`
- `resolved_at`
- `source_contact_id`
- `last_prompted_at`

Relationships:
- many-to-one with `Load`
- one-to-many with `LoadAction`

Validation rules:
- `state` must transition only through defined lifecycle states.
- An unanswered request can be reopened only if the previous request remains relevant and the SOP requires it.

### LoadAction

Represents any outbound action or internal work item created by the agent.

Fields:
- `action_id`
- `load_id`
- `action_type`: sms_send, email_send, slack_alert, tms_update, hero_task, timer_set, status_update
- `status`: queued, sent, delivered, failed, completed, no_action
- `trigger_event_id`
- `sop_rule_id`
- `actor`: agent, hero, broker, internal_system
- `payload_json`
- `created_at`
- `completed_at`

Relationships:
- many-to-one with `Load`
- many-to-one with `LoadEvent` as trigger
- many-to-one with `SOPRule`

Validation rules:
- `action_type` must match a permitted action catalog.
- No-action decisions must still be logged as an explicit action state when the system intentionally chooses to do nothing.

### DecisionRecord

Stores the system's decision for one processed event, the LLM's constrained proposal when available, and the deterministic validation/review outcome. It is immutable audit evidence; it does not itself authorize execution.

Fields:
- `decision_id`
- `load_id`
- `trigger_event_id`
- `sop_rule_id`
- `disposition`: propose_action, no_action; empty only if a provider/system failure prevented a usable system decision
- `model_disposition`: propose_action, no_action, abstain; nullable when no usable model response was returned
- `review_status`: not_required, required, resolved
- `review_reason`: nullable explanation for required review
- `sop_document_id`
- `sop_version`
- `sop_content_hash`
- `ruleset_version`
- `sop_section_ref`
- `supporting_passage`
- `load_evidence_refs`
- `proposed_actions`: zero or more action-catalog entries with validated payloads
- `provider_id`
- `model_id`
- `provider_request_id`
- `prompt_schema_version`
- `uncertainty_level`
- `validation_status`: accepted, rejected, provider_failed
- `validation_reasons`
- `created_at`

Relationships:
- many-to-one with `Load`
- many-to-one with `LoadEvent`
- many-to-one with the selected `SOPRule`, when a proposal was produced
- references the applicable `SOPDocument`
- accepted records may produce one or more `LoadAction` records

Validation rules:
- The cited SOP document/version and section must belong to the selected active SOP; client-specific precedence is resolved before interpretation.
- The supporting passage must be present in that SOP version, and proposed actions must use the approved action catalog and valid payload schema.
- Deterministic validation must confirm current request state, contact identity, channel availability, and permitted state transitions before accepting execution.
- `no_action` disposition must have no proposed actions. Unclear, unrelated, or non-applicable events must be stored as `no_action`; an abstain signal is recorded separately as `model_disposition=abstain` with `review_status=required`.
- Review status may be `required` alongside a no-action decision, but it never authorizes an external action.
- Malformed or unsupported proposals must be rejected or routed for human review; provider failures must be recorded explicitly and must not be treated as a valid no-action result unless the event independently qualifies as unclear, unrelated, or non-applicable under the system's decision policy.
- SOP citation and proposal fields may be empty when no usable model proposal was returned; the system decision remains `no_action` for an unclear/non-applicable event, with the provider failure recorded separately in `validation_status` and `validation_reasons`.
- Provider credentials and secret values must never be stored in this record.

### SOPRule

Indexes an SOP section for deterministic applicability and precedence selection. The section's natural-language policy is interpreted by the LLM; this record does not encode its condition or action as executable logic.

Fields:
- `rule_id`
- `client_id`
- `scope`: standard, client_override, load_type_override
- `load_stage`
- `section_ref`
- `applicable_event_types`
- `priority`
- `version`
- `effective_from`
- `effective_to`

Relationships:
- many-to-one with `SOPDocument`

Validation rules:
- applicability and precedence are resolved before model interpretation; client-specific procedures take precedence over standard procedures for matching scope and stage.
- changes must be versioned and auditable.

### SOPDocument

Represents a Markdown SOP file for a client or standard procedure.

Fields:
- `document_id`
- `client_id`
- `chapter_name`
- `version`
- `content_markdown`

Relationships:
- one-to-many with `SOPRule`

### DocumentAttachment

Represents a BOL, POD, rate confirmation, receipt, or other document.

Fields:
- `document_id`
- `load_id`
- `document_type`
- `source_uri`
- `content_type`
- `hash`
- `status`: received, classified, extracted, rejected
- `extracted_values_json`

Relationships:
- many-to-one with `Load`

Validation rules:
- `document_type` must be recognized before field extraction is accepted.
- extracted values must be versioned and reviewable for audits.

## Relationship Summary

- A `Load` has many `Stop`, `LoadEvent`, `LoadAction`, `OpenRequest`, and `DocumentAttachment` records.
- A `LoadEvent` can trigger one or more `LoadAction` records or create an `OpenRequest`.
- A `LoadEvent` produces a `DecisionRecord`; only a validated decision can create `LoadAction` records.
- `OpenRequest` remains active until it is answered, expired, or cancelled.
- `SOPRule` defines the decision path that creates `LoadAction` records and optionally updates the `Load` stage.
- `DocumentAttachment` classification and extraction support operational decisions after pickup and delivery.

## state transitions

The primary lifecycle is defined by the load stage transitions:

- `dispatched -> to_pickup -> at_pickup -> to_delivery -> at_delivery -> delivered`
- Intermediate stops repeat the pickup/delivery sequence as required.
- Delay-risk and unresolved request states can create additional operational states without leaving the main lifecycle.

## Data quality expectations

- All actions must be traceable to an event and a rule.
- No message can be treated as a valid answer unless its content matches the active request semantics.
- Timer expiration is a first-class event in the operational ledger.
- All persisted data must be searchable by load ID, client ID, and stage to support audits and operational review.
