# Quickstart: Load SOP Monitoring Agent

## Prerequisites

- Python 3.11+
- PostgreSQL for running the ingestion API
- A sample load and, when sending messages, contacts with valid operational channels

The automated tests use in-memory stores, mock action adapters, and a
`SequenceInterpreter`; they do not require PostgreSQL or a provider API key.

## Local setup and tests

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
python -m pytest tests/evals -q
```

The first command creates a virtual environment; if `.venv` already exists,
activate it and continue with the installation and test commands.

## Run the event-ingestion API

The API persists accepted events to PostgreSQL. Create a database, apply the
schema, and provide its connection URL:

```sh
export FREIGHT_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE'
psql "$FREIGHT_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/001_initial_schema.sql
psql "$FREIGHT_DATABASE_URL" -v ON_ERROR_STOP=1 -c \
  "INSERT INTO loads (broker_id, load_id, broker_name, current_stage) VALUES ('broker_a', '481207', 'Example Broker', 'at_pickup') ON CONFLICT (broker_id, load_id) DO NOTHING"
uvicorn app.api:app --app-dir src --host 127.0.0.1 --port 8000
```

In another terminal, submit an event for that seeded load:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8000/v1/events/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "broker_id": "broker_a",
    "load_id": "481207",
    "event_type": "ping",
    "source_channel": "system",
    "payload": {},
    "occurred_at": "2026-10-13T07:12:00Z"
  }'
```

The expected response is HTTP 202 with `accepted: true`, a generated `event_id`,
and `status: "queued_for_processing"`. This endpoint enqueues the event; it does
not itself run the load worker.

## LLM provider and mock configuration

The provider-neutral settings are supplied through environment variables:

```sh
export FREIGHT_LLM_PROVIDER='your-provider'
export FREIGHT_LLM_MODEL_ID='your-model'
export FREIGHT_LLM_API_KEY='provided-secret'
export FREIGHT_LLM_MAX_INPUT_TOKENS=12000
export FREIGHT_LLM_MAX_OUTPUT_TOKENS=2048
export FREIGHT_LLM_MAX_CALLS_PER_LOAD=20
export FREIGHT_LLM_MAX_COST_PER_LOAD_USD=1.0
```

`FREIGHT_LLM_API_KEY` is secret configuration and must not be placed in
source control, prompts, logs, or persisted decision records. Current tests use
mock interpreters and need none of these variables. A provider-specific client
adapter and worker/service composition are not included yet, so setting these
values alone does not enable live LLM decisions or event processing.

## Validation scenarios

The integration suite validates these scenarios using deterministic mock
interpreters and in-memory repositories:

1. **High delay risk**: request a driver ETA, schedule the follow-up, process a
   clarification without resolving the ETA request, then escalate after the
   follow-up expires. Each action is correlated to its event and SOP rule.
2. **No action and abstention**: unrelated or unclear messages persist an
   explicit system `no_action`; model abstention is stored separately with
   review required and never authorizes an external action.
3. **SOP override**: a matching client override takes precedence over the
   standard SOP and suppresses the standard action when specified.
4. **Load lifecycle and documents**: stage transitions are guarded, ping risk
   is derived from ETA versus the appointment window when authoritative risk
   is absent, and supported document types are classified before extraction.
5. **Timer resumption**: a timer reloads its originating event and request
   context; a timer for a resolved request cannot execute an action.
6. **Degraded action path**: missing/invalid contacts and unavailable adapters
   persist a failed action reason, route the accepted proposal to review, and
   surface the execution failure rather than recording a successful no-action.

## Validation limitations

The full automated test suite and evaluation suite are the runnable local
checks. PostgreSQL and `psql` are not available in the implementation
environment, so the migration and live API smoke test have not been executed
against a database. A provider-specific LLM adapter and production worker
composition remain required before live event-to-action processing is
available.
