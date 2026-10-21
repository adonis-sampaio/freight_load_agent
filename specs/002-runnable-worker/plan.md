# Implementation Plan: Runnable Background Worker

**Branch**: `002-runnable-worker` | **Date**: 2026-10-20 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-runnable-worker/spec.md`

## Summary

Provide an independently runnable background process that composes the existing PostgreSQL repositories, context builder, decision engine, validators, and `LoadWorker`, then continuously processes queued events. The first runnable mode is explicitly a safe demonstration: it uses a deterministic no-action interpreter and non-delivering action adapters, records outcomes, handles idle polling and graceful shutdown, and reclaims stale work after process interruption. It does not claim to provide live LLM inference or external communications.

## Technical Context

**Language/Version**: Python 3.11+ (project requirement; current local environment is Python 3.12)

**Primary Dependencies**: Existing project dependencies in `pyproject.toml`, including `asyncpg`, `pydantic-settings`, and `structlog`; no new runtime dependency is planned.

**Storage**: Existing PostgreSQL schema and repositories; add a forward migration to track when an event enters processing so stale claims can be recovered.

**Testing**: pytest and pytest-asyncio, using focused unit and integration tests; local PostgreSQL-backed validation is required for the end-to-end run guide.

**Target Platform**: Linux server or hosted background-worker service, including Render Background Worker.

**Project Type**: Python backend service with a separate background-process entry point.

**Performance Goals**: A newly queued event is discovered within 10 seconds while idle; the worker must not busy-poll. One worker iteration processes at most one event.

**Constraints**: Require `FREIGHT_DATABASE_URL`; keep API and worker separate; retain deterministic SOP validation; make demonstration actions non-delivering; use graceful signal-based shutdown; report operational state in logs; do not silently strand interrupted events.

**Scale/Scope**: Single-process polling worker for demonstration and low-volume operation. Horizontal scaling remains compatible with atomic event claiming, but production delivery guarantees and high-volume tuning are outside this feature.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **SOP-First Decisioning**: PASS. Worker composition calls the existing context builder and validator; the demo interpreter cannot bypass them.
- **Evidence-Backed Memory**: PASS. Events, decisions, and actions remain persisted in PostgreSQL; stale claimed events are recovered visibly.
- **Human-in-the-Loop Escalation**: CONSTRAINED PASS. Review-required outcomes are surfaced as operator-visible warnings in worker logs for the demonstration; this is not a production review workflow and must not be presented as one.
- **Traceable Actioning**: PASS. Existing decision/action ledgers remain authoritative; demo action acknowledgements are marked as simulated in logs and synthetic identifiers.
- **Reliability, Cost, and Scale Discipline**: PASS. Demo mode makes no model-provider calls; polling and failure waits are bounded; no unbounded retry loop is introduced.
- **Operational Safety Constraints**: PASS. No real outbound integrations are configured in demo mode; proposed actions continue to pass contact and SOP validation before reaching non-delivering adapters.
- **Development Workflow**: PASS. Focused tests cover no-action, proposed-action simulation, failure, recovery, and shutdown scenarios.

The demo-only limitation on production review routing is justified and recorded under Complexity Tracking; it blocks live operational use until a real review route exists.

## Project Structure

### Documentation (this feature)

```text
specs/002-runnable-worker/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── worker-runtime.md
└── tasks.md
```

### Source Code (repository root)

```text
src/
├── workers/
│   ├── load_worker.py                 # Existing single-event processor
│   ├── run_worker.py                  # New executable entry point and polling lifecycle
│   ├── worker_composition.py          # New dependency composition and safe demo adapters
│   └── seed_demo_data.py              # New repeatable load/SOP demonstration data setup
├── app/
│   ├── agent/
│   │   ├── decision_context.py        # Existing SOP-backed context builder
│   │   └── decision_engine.py         # Extend to distinguish demo from billable provider calls
│   ├── services/
│   │   └── action_executor.py         # Map explicit simulated acknowledgements to simulated records
│   └── memory/
│       ├── database.py                # Existing PostgreSQL pool lifecycle
│       └── load_timeline.py           # Extend claims with timestamps and stale-claim recovery
├── models/
│   └── action.py                      # Add explicit simulated action status
└── adapters/
    └── demo_action_adapters.py        # New non-delivering acknowledgements for demo mode

migrations/
└── 002_worker_claim_recovery.sql      # Add processing claim timestamp/index as needed

examples/
└── demo_sop.md                        # Sample operational policy used by the demo seed command

tests/
├── unit/
│   └── test_worker_runtime.py         # New polling, shutdown, startup and failure behavior
└── integration/
    └── test_worker_runtime.py         # New database-backed event-to-decision smoke coverage
```

**Structure Decision**: Keep the existing `src/` Python package layout. Add an executable worker module under `src/workers/`; preserve `LoadWorker.run_once()` as the one-event processing unit and put the continuous lifecycle/composition at the worker boundary. This avoids coupling the API lifespan to worker polling and lets Render run the API and worker as separate services.

## Complexity Tracking

| Constraint | Justification | Limitation |
|---|---|---|
| Production human-review routing is not delivered | The requested milestone is a safe runnable demonstration and the repository has no configured review destination or review UI. A visible log handoff lets an operator observe a required review without inventing an external integration. | Not suitable for operational production use. A real human review destination and resolution workflow remain required before live decisioning. |

## Design Decisions

- **Demo inference**: Use a deterministic, explicitly identified no-action interpreter by default. It proves event intake, SOP context construction, validation, and durable decision recording without implying a live model call happened or requiring a paid secret. Demo decisions must not reserve or record billable LLM usage.
- **Demo side effects**: Use dedicated non-delivering adapters, not the existing unit-test mocks whose acknowledgements can look like real sends. Synthetic acknowledgements and logs must identify simulation; no network clients or external credentials are read in demo mode.
- **Review routing**: Add a minimal log-backed review-router implementation for the runnable demo because the repository currently defines only a review protocol. Durable review workflow/UI remains out of scope; worker logs are an operator-visible demo handoff, not production escalation.
- **Interrupted claims**: Record a processing-start timestamp when claiming an event. Requeue claims older than a documented stale threshold before polling. This is at-least-once processing; demo mode has no real external side effects. Production integrations will need idempotency before enabling delivery.
- **Failure policy**: Log event-scoped processing errors and continue to later events when PostgreSQL remains usable. A database/claiming failure is surfaced as a worker health failure and retried only with bounded delay; startup configuration or connection failure exits non-zero.
- **Shutdown**: Handle termination signals by setting a stop request, finish the current `run_once()`, skip further claims, and close the shared database pool in `finally`.
- **Run contract**: Install the package and run `python -m workers.run_worker`; configuration, defaults, lifecycle behavior, and Render service arrangement are documented in [contracts/worker-runtime.md](./contracts/worker-runtime.md).

## Risks and Mitigations

- **Duplicate processing after a crash**: Stale-claim recovery is at-least-once. Keep demo adapters non-delivering and document that live action adapters require idempotency keys and delivery reconciliation.
- **Incorrect demo expectations**: Identify the deterministic interpreter, simulated action adapters, and review logging as demo-only in startup logs and quickstart instructions.
- **Missing demonstration data**: Document a reproducible PostgreSQL setup with a load and applicable SOP rule/document; distinguish a successfully consumed event with no applicable SOP from an LLM decision.
- **Provider unavailable/not implemented**: Do not present environment variables as sufficient for live inference; fail configuration clearly if a non-demo mode is requested before a provider implementation exists.

## Constitution Check (Post-Design)

- **SOP-First Decisioning**: PASS. Demo proposals still traverse the normal validation path; test coverage includes rejected actions.
- **Evidence-Backed Memory**: PASS. The claim timestamp extends existing persisted event state and enables recovery without replacing the existing timeline.
- **Human-in-the-Loop Escalation**: CONSTRAINED PASS. Review-required decisions produce an operator-visible log notification in demo mode; the missing production routing is documented as a scoped limitation above.
- **Traceable Actioning**: PASS. Decision and action records are preserved; simulated acknowledgements remain distinguishable from real delivery.
- **Reliability, Cost, and Scale Discipline**: PASS. No LLM costs in demo mode; bounded polling and stale-claim handling are specified; production guarantees are explicitly limited.
- **Operational Safety Constraints**: PASS. Live external integrations are unavailable in demo mode and validator/contact checks remain in force.

No unjustified post-design gate failures; the demo-only human-review limitation is recorded above.
