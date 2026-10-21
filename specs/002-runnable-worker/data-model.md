# Data Model: Runnable Background Worker

## Existing entities

### Load event

Represents an inbound event linked to one broker and load.

| Field | Meaning | Validation / lifecycle |
|---|---|---|
| `event_id` | Unique event identifier | Non-empty; primary key |
| `broker_id`, `load_id` | Tenant and load scope | Must reference the corresponding load |
| `event_type`, `source_channel` | Event classification and origin | Must be supported enum values |
| `payload_json` | Event-specific structured data | JSON object |
| `processing_status` | Queue lifecycle state | `queued` → `processing` → `completed` or `failed` |
| `occurred_at`, `ingested_at` | Event and ingestion timestamps | Timezone-aware |
| `correlation_id` | Related workflow identifier | Optional |
| `processing_started_at` | Start time of current claim | New nullable timestamp; populated on claim, cleared on completion/failure/requeue |

**Recovery transition**: A `processing` event whose claim timestamp predates the configured stale threshold transitions back to `queued`. This is at-least-once recovery; it is safe for the demo only because outbound adapters are non-delivering. The recovery operation must not reset a recent claim owned by another active worker.

### Load and SOP context

Existing persisted inputs used by the context builder:

- **Load**: Broker-scoped load identity, current stage/status, allowlisted operational facts, and stops.
- **Contact**: Active contact identity, role, channels, and destination reference; used only after proposal validation.
- **SOP document**: Immutable policy content, version, and content hash.
- **SOP rule**: Client/load scope, stage, applicable event types, section reference, priority, and effective period.
- **Open request**: Unresolved operational question and optional timer/correlation data.

The context builder must select the active applicable rule and reject missing or ambiguous matching policy rather than guessing.

### Decision outcome

Represents the recorded result for a processed event. Existing records include the triggering event, selected rule/document/version/hash when present, disposition, review status, validation status/reasons, supporting evidence, proposed actions, and creation time. Demo decisions must be distinguishable from provider-generated decisions; the deterministic demo interpreter must not be described as a live LLM.

### Action record

Represents an accepted no-action record or a validated action proposal. In demo mode, action adapters create no external effects and return synthetic identifiers. A simulated action uses an explicit `simulated` action status and operator-facing output marks it as simulation; it must not be reported as a real delivery.

## State transitions

```text
queued ──claim──> processing ──success──> completed
                         ├────error────> failed
                         └─stale claim─> queued
```

No-action decisions, including no-applicable-SOP outcomes, are completed processing outcomes when their persistence succeeds. A failure to persist the decision or finish the event is a processing failure, not a successful no-action.

## Persistence changes

Add `processing_started_at` to the initial schema for fresh installations and provide a forward-only, idempotent migration for existing installations. Extend the persisted action-status constraint to allow the explicit `simulated` status. Add a partial index for stale processing claims if required by the recovery query. Preserve all other existing status checks and event fields. Apply migration changes explicitly to existing deployments and fresh local databases.
