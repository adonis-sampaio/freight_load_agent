# Quickstart: Runnable Background Worker

This guide validates the worker after implementation. The first mode is a deterministic safe demonstration: it does not make a real LLM call or send real messages, tasks, or timers.

## Prerequisites

- Python 3.11+
- PostgreSQL with the base schema and worker recovery migration applied
- A database connection string with permission to read and update loads/events/SOP data and insert decisions/actions

## Install

From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

Set the database URL in the shell or deployment environment. Do not commit credentials:

```sh
export FREIGHT_DATABASE_URL='postgresql://USER:PASSWORD@HOST:5432/DATABASE'
```

Apply the project's migrations in order. For a local database, the initial schema can be applied with:

```sh
psql "$FREIGHT_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/001_initial_schema.sql
```

Apply the worker claim-recovery migration after the initial schema. It must be idempotent so it is safe for fresh databases whose initial schema already has the recovery field and for existing installations. The migration filename and command are finalized during implementation.

## Prepare demonstration data

Seed a broker-scoped load and a standard SOP document/rule whose stage and event type match the demonstration event:

```sh
python -m workers.seed_demo_data
```

The seed command should be repeatable and target only the configured demo database. It must not overwrite conflicting operational records. Do not use production customer or driver data for an interview demo.

## Start the API and worker

Run these in separate terminals, using the same database:

```sh
# Terminal 1: event-ingestion API
uvicorn app.api:app --app-dir src --host 127.0.0.1 --port 8000

# Terminal 2: background worker
python -m workers.run_worker
```

For Render, run the API as a Web Service and the worker as a separate Background Worker. Configure `FREIGHT_DATABASE_URL` as a secret environment variable on both services. Set the worker start command to `python -m workers.run_worker`.

## Submit and verify an event

Submit an event for the seeded load using the existing endpoint:

```sh
curl --fail-with-body -X POST http://127.0.0.1:8000/v1/events/ingest \
  -H 'Content-Type: application/json' \
  -d '{
    "broker_id": "broker_a",
    "load_id": "481207",
    "event_type": "ping",
    "source_channel": "system",
    "payload": {},
    "occurred_at": "2026-10-20T12:00:00Z"
  }'
```

Expected result: the API returns HTTP 202 with an event ID; the worker logs that it processed the event; the event becomes `completed`; and a corresponding decision is persisted. The deterministic demo decision is explicitly marked as demo/no-action. No external message is sent.

Use PostgreSQL to inspect the result:

```sql
SELECT event_id, processing_status, processing_started_at
FROM load_events
WHERE broker_id = 'broker_a' AND load_id = '481207'
ORDER BY ingested_at DESC
LIMIT 5;

SELECT trigger_event_id, disposition, validation_status, validation_reasons
FROM decision_records
WHERE broker_id = 'broker_a' AND load_id = '481207'
ORDER BY created_at DESC
LIMIT 5;
```

If no SOP rule applies, the worker should record an explicit `no_applicable_sop` no-action decision. If the load is missing or the database operation fails, the event should be visibly failed and the worker should report the error.

## Run focused tests

```sh
python -m pytest -q tests/unit/test_worker_runtime.py tests/unit/test_load_worker.py
python -m pytest -q tests/integration/test_worker_runtime.py
```

The integration test requires a PostgreSQL test database configured for the test harness. Tests for proposed external actions must verify that demo adapters return simulated acknowledgements and perform no network I/O.

## Stop the worker

Send the normal termination signal (Ctrl+C locally or stop/restart the Render Background Worker). The process should finish its current event, claim no additional events, close the PostgreSQL pool, and log shutdown.

See [worker-runtime.md](contracts/worker-runtime.md) for the process contract and [data-model.md](data-model.md) for event recovery semantics.
