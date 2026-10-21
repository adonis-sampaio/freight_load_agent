# Worker Runtime Contract

## Purpose

The worker is a separate process from the FastAPI ingestion service. It reads events already persisted by the API and records a decision outcome. The first runtime mode is demonstration-only and does not call an LLM provider or deliver external actions.

## Start command

After installing the project and applying database migrations:

```sh
python -m workers.run_worker
```

The process exits non-zero if required configuration is missing or the database cannot be opened. It logs readiness only after successful startup.

## Configuration

| Setting | Required | Behavior |
|---|---:|---|
| `FREIGHT_DATABASE_URL` | Yes | PostgreSQL connection string; supply as a secret in hosted environments |
| `FREIGHT_LOG_LEVEL` | No | Existing application logging level |
| `FREIGHT_WORKER_POLL_INTERVAL_SECONDS` | No | Defaults to 2 seconds; must be greater than 0 and at most 10 seconds |
| `FREIGHT_WORKER_STALE_CLAIM_SECONDS` | No | Defaults to 900 seconds; must be at least 60 seconds and exceed the expected processing duration |
| `FREIGHT_WORKER_DB_MAX_RETRIES` | No | Defaults to 5; accepts 0-20 retries and uses exponential backoff capped at 30 seconds |

LLM variables do not enable a real provider in this feature. If a live-provider mode is not implemented, the runtime must not imply that model inference occurred.

## Runtime behavior

1. Open one database pool and construct the timeline, load, SOP, request, decision, and action repositories from it.
2. Compose the context builder, deterministic demo interpreter, validator, decision engine, log-backed review router, and non-delivering action adapters. Demo interpreter calls must not consume or report live LLM usage budget.
3. Recover eligible stale processing claims, then repeatedly invoke one-event processing.
4. Sleep for the configured bounded interval when no event was available.
5. Log an event-scoped processing failure and continue when the database remains usable. Database-level poll failures are visible and use bounded retry behavior; an unrecoverable failure exits non-zero.
6. On termination signal, stop claiming new work after the current event, close the database pool, and exit.

Review-required decisions produce an operator-visible warning in the worker logs. This demo notification is not a production human-review workflow.

## External effects and safety

- Demo mode performs no outbound network communication and requires no external action credentials.
- Proposed actions still pass the existing SOP, contact, and deterministic validation checks.
- Action acknowledgements and logs use synthetic demo identifiers and explicitly identify simulation; they must not claim that a real message or task was delivered.
- The runtime records deterministic no-action decisions as demo decisions, not LLM decisions, and does not reserve billable LLM usage for them.
- Live provider and action integrations are outside this contract.

## Event processing contract

The event state flow, decision record, and recovery semantics are documented in [data-model.md](../data-model.md). Claiming must remain atomic under concurrent worker instances. Recovery provides at-least-once processing, not exactly-once external delivery.
