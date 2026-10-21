# Research: Runnable Background Worker

**Date**: 2026-10-20

The feature's technical choices were resolved by inspecting the existing worker, database repositories, action adapters, application settings, tests, and project constitution. No unresolved technical clarifications remain.

## Worker lifecycle and composition

- **Decision**: Add a separate executable module and composition root under `src/workers/`. Open the existing `Database` pool from `FREIGHT_DATABASE_URL`, construct PostgreSQL-backed repositories and services once, and keep calling the existing one-event `LoadWorker.run_once()` method.
- **Rationale**: `LoadWorker` already coordinates load updates, context construction, decision processing, and event completion. The API lifespan deliberately only manages ingestion and database lifecycle; embedding the worker there would make independent Render service operation and shutdown less clear.
- **Alternatives considered**: Starting a worker task from FastAPI lifespan was rejected because the API and worker need separate process scaling, restart, and health boundaries. Duplicating event orchestration in a new worker was rejected because it would drift from `LoadWorker`.
- **Code evidence**: `src/workers/load_worker.py`, `src/app/api.py`, `src/app/memory/database.py`.

## Deterministic demonstration decisioning

- **Decision**: Default the runnable demonstration to a deterministic no-action interpreter that conforms to the decision-engine interface and clearly identifies itself as demo behavior. Demo decisions bypass LLM call/cost reservation and latency accounting while retaining decision audit fields that identify the demo interpreter. Do not add a provider-specific LLM integration in this feature.
- **Rationale**: Settings define provider fields and the project wraps a provider protocol, but no provider client is implemented. A deterministic no-action path can prove event claims, SOP selection, schema validation, audit persistence, and no-action behavior without implying an LLM call happened, consuming a model budget, or requiring a paid secret.
- **Alternatives considered**: Treating `FREIGHT_LLM_PROVIDER` and `FREIGHT_LLM_API_KEY` as if they activated a provider was rejected because no such client exists. Copying an integration-test scripted interpreter into production as if it were an LLM was rejected as misleading. A live provider adapter is a separate, explicitly out-of-scope capability.
- **Code evidence**: `src/adapters/llm_provider_adapter.py`, `src/app/services/llm_sop_interpreter.py`, `src/config/settings.py`, `src/app/agent/decision_engine.py`, `tests/integration/agent_support.py`.

## Safe action simulation

- **Decision**: Use dedicated demo action adapters that perform no network I/O, return synthetic identifiers and non-delivery acknowledgements, and emit explicit simulation logs. Exercise valid proposed-action flow in tests through injectable interpreters and these adapters.
- **Rationale**: Existing mock adapters are test utilities and return statuses such as `sent` or `created`, which could falsely imply that a real external action occurred if directly used for an interview demo.
- **Alternatives considered**: Reusing `MockSMSAdapter`, `MockEmailAdapter`, `MockTaskAdapter`, or `MockTimerAdapter` in the runnable mode was rejected because their acknowledgements resemble real delivery. Configuring real external adapters was rejected because live integrations and credentials are not part of this feature.
- **Code evidence**: `src/adapters/{sms,email,task,timer}_adapter.py`, `src/app/services/action_executor.py`, `src/models/action.py`.

## Review-required outcomes

- **Decision**: Add an operator-visible log-backed review router for the demo worker, and explicitly block claims that this constitutes production review handling.
- **Rationale**: The existing code defines a review protocol but no concrete destination, queue, or user interface. The demo can expose review-required decisions to the person operating Render logs without inventing an integration; production decisioning still requires an owned review route and resolution workflow.
- **Alternatives considered**: Silently ignoring review-required results was rejected because it violates the project's human-in-the-loop principle. Claiming the logs are a complete production review workflow was rejected as misleading.
- **Code evidence**: `src/app/services/review_router.py`, `src/app/agent/decision_engine.py`, project constitution section III.

## Event recovery and concurrency

- **Decision**: Extend event claiming with a processing-start timestamp and a stale-claim recovery operation. Requeue only claims older than a documented threshold; continue to use the repository's atomic `FOR UPDATE SKIP LOCKED` claim so concurrent workers do not claim the same queued event.
- **Rationale**: Current claim logic changes `queued` to `processing`, but there is no timestamp or reclaim path, so a process crash can leave an event stuck indefinitely. The safe demo has no external delivery, making bounded at-least-once replay acceptable.
- **Alternatives considered**: Never reclaiming claims was rejected because it violates recoverability. Resetting every `processing` row at every startup was rejected because another active worker may own it. Exactly-once delivery was rejected as not achievable with the current external-action model and outside demonstration scope.
- **Code evidence**: `src/app/memory/load_timeline.py`, `migrations/001_initial_schema.sql`, `src/workers/load_worker.py`.
- **Constraint**: Real production adapters must add action-level idempotency and reconciliation before stale event replay is allowed to cause external effects.

## Polling, failure handling, and shutdown

- **Decision**: Use a bounded idle sleep after an empty poll, signal-driven graceful stop, per-event exception logging/continuation when the database is usable, and bounded retry or process failure for database-level polling errors. Always close the pool in a `finally` path.
- **Rationale**: The existing worker raises processing failures after marking the event failed. The runtime must distinguish those event-scoped failures from loss of its database dependency and must not tight-loop while idle.
- **Alternatives considered**: An unbounded tight polling loop was rejected for resource usage. Swallowing all exceptions and continuing was rejected because it could hide an unavailable database or failed status writes. Abrupt termination during an active event was rejected because it bypasses normal finalization and cleanup.
- **Code evidence**: `src/workers/load_worker.py`, `src/app/memory/database.py`, `src/app/memory/load_timeline.py`.

## Dependency and hosting approach

- **Decision**: Use existing Python, asyncio, PostgreSQL, and logging dependencies. Document Render as a separate Background Worker using the same environment variables/database and a package-module start command.
- **Rationale**: `pyproject.toml` already contains the runtime dependencies and packages under `src/`; Render is the deployment context previously identified by the user. A separate worker process is supported without adding a queue broker.
- **Alternatives considered**: Adding a third-party queue/broker was rejected for this single-process demonstration because PostgreSQL already durably stores and atomically claims events. Adding a new CLI framework was rejected because a Python module entry point is sufficient.
- **Code evidence**: `pyproject.toml`, `src/app/memory/load_timeline.py`, existing deployment conversation context.
