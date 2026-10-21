# Tasks: Runnable Background Worker

**Input**: Design documents from `/specs/002-runnable-worker/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/worker-runtime.md](./contracts/worker-runtime.md), [quickstart.md](./quickstart.md)

**Tests**: Include focused unit and PostgreSQL integration tests as required by the plan and the independently testable acceptance scenarios.

**Organization**: Tasks are grouped by user story to support incremental implementation and verification.

## Format: `- [ ] [ID] [P?] [Story?] Description with file path`

- **[P]**: Work can proceed in parallel because it touches independent files and has no incomplete-task dependency.
- **[Story]**: User story served by the task; setup, foundational, and polish tasks omit this label.
- Every task names the file or files it changes or validates.

## Phase 1: Setup

**Purpose**: Add reviewable demonstration policy input before the data seeder and worker are composed.

- [X] T001 [P] Add a minimal, clearly labeled standard pickup-event SOP with a no-action demonstration rule and matching section reference in `examples/demo_sop.md`.

## Phase 2: Foundational

**Purpose**: Prepare shared persistence and domain support required by safe demo behavior and event recovery.

- [X] T002 Add nullable `processing_started_at` to fresh schema `migrations/001_initial_schema.sql`; create idempotent forward migration `migrations/002_worker_claim_recovery.sql` that adds the same column and updates the `load_actions.status` constraint to permit `simulated`, preserving all existing statuses.
- [X] T003 [P] Add `ActionStatus.SIMULATED` in `src/models/action.py` and map adapter acknowledgement status `simulated` to it in `src/app/services/action_executor.py`.

**Checkpoint**: Fresh and existing databases can represent stale worker claims and explicitly simulated actions.

## Phase 3: User Story 1 - Start event processing for a demonstration (Priority: P1) 🎯 MVP

**Goal**: Run the existing event-to-decision path against PostgreSQL with deterministic demo decisions and no real outbound effects.

**Independent Test**: With the migrations applied and demo data seeded, start the worker, submit an event through the API for the prepared load, and verify that the event reaches a terminal state with an auditable decision while no provider call or external action occurs.

### Implementation for User Story 1

- [X] T004 [US1] Implement no-network SMS, email, task, and timer adapters that return synthetic `simulated` acknowledgements and explicit simulation logs in `src/adapters/demo_action_adapters.py`.
- [X] T005 [US1] Implement a deterministic no-action decision interpreter and an operator-visible log-backed review router in `src/workers/worker_composition.py`; mark demo decisions clearly and do not present log review warnings as production escalation.
- [X] T006 [US1] Update `DecisionEngine` in `src/app/agent/decision_engine.py` to distinguish demo interpreters from provider-backed interpreters, skip LLM call/cost reservation and latency reporting for demo calls, and keep all proposals on the existing validation path.
- [X] T007 [US1] Compose the existing PostgreSQL repositories, decision context builder, validator, decision engine, review router, and demo action adapters into `LoadWorker` in `src/workers/worker_composition.py`, sharing one opened database pool.
- [X] T008 [US1] Add executable module `src/workers/run_worker.py` that opens `Database`, builds the demo worker through `src/workers/worker_composition.py`, and repeatedly calls `LoadWorker.run_once()` with an idle wait when no event is available.
- [X] T009 [US1] Add an idempotent, demo-database-only seed command in `src/workers/seed_demo_data.py` that creates the documented sample load and applicable SOP document/rule from `examples/demo_sop.md`, refusing conflicting records instead of overwriting them.
- [X] T010 [US1] Add focused unit tests in `tests/unit/test_worker_composition.py` covering deterministic demo decision metadata, no billable LLM reservation, simulation status, and the absence of network delivery.
- [X] T011 [US1] Add a PostgreSQL-backed event-to-decision integration test in `tests/integration/test_worker_runtime.py` that seeds or prepares a load/SOP, queues an event, runs one worker iteration, and verifies terminal event state plus a persisted demo decision.

**Checkpoint**: A reviewer can seed demonstration data, run the worker, submit a matching event, and inspect the recorded outcome without real LLM or external-service credentials.

## Phase 4: User Story 2 - Operate and stop the worker safely (Priority: P2)

**Goal**: Make the worker's polling process observable, bounded, recoverable, and safe to stop.

**Independent Test**: Start with missing and valid database configuration; verify readiness/error behavior, idle polling and event-level continuation, recover a stale claim, then request shutdown while idle and while an event is being processed and verify database cleanup.

### Implementation for User Story 2

- [X] T012 [US2] Add validated `FREIGHT_WORKER_POLL_INTERVAL_SECONDS` (default 2, greater than 0 and at most 10), `FREIGHT_WORKER_STALE_CLAIM_SECONDS` (default 900, minimum 60), and `FREIGHT_WORKER_DB_MAX_RETRIES` (default 5, range 0-20) settings in `src/config/settings.py`; reject invalid values at startup and cap exponential database retry backoff at 30 seconds.
- [X] T013 [US2] Update `LoadTimelineRepository` in `src/app/memory/load_timeline.py` to timestamp atomic event claims, clear the timestamp on completion/failure, and atomically requeue only `processing` events older than the configured stale threshold.
- [X] T014 [US2] Extend `src/workers/run_worker.py` to log startup/readiness/shutdown, validate database availability before readiness, recover stale claims, install termination-signal handling, finish the active event before stopping, close the pool in `finally`, continue after event-scoped failures, and use bounded retry/exit behavior for database poll failures.
- [X] T015 [US2] Add worker lifecycle unit tests in `tests/unit/test_worker_runtime.py` for missing configuration, invalid settings, idle sleep, event-scoped failure continuation, bounded database retry, and graceful shutdown without a second claim.
- [X] T016 [US2] Add repository recovery tests in `tests/unit/test_load_timeline.py` and PostgreSQL integration coverage in `tests/integration/test_worker_runtime.py` proving recent claims are preserved, stale claims are requeued, and concurrent workers do not claim the same event.

**Checkpoint**: The worker can be operated as a persistent process, stopped cleanly, and restarted without silently stranding claimed events.

## Phase 5: Polish & Cross-Cutting Concerns

**Purpose**: Ensure operators can reproduce the demo and understand its safety and deployment limits.

- [X] T017 [P] Replace placeholders in `specs/002-runnable-worker/quickstart.md` with implemented setting names, migration commands, seed command behavior, expected database outcomes, and test prerequisites.
- [X] T018 [P] Update `README.md` with local worker and Render Background Worker instructions, clearly distinguishing deterministic demo decisions from live LLM decisioning and simulated actions from real delivery.
- [X] T019 Run the focused unit and PostgreSQL integration commands in `specs/002-runnable-worker/quickstart.md`, then correct any mismatched instructions or expected outcomes in `specs/002-runnable-worker/quickstart.md`.

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; `T001` can begin immediately.
- **Foundational (Phase 2)**: Begins after setup; `T002` and `T003` can proceed in parallel. All user stories require both.
- **User Story 1 (Phase 3)**: Depends on the foundational phase. `T004` and `T005` can proceed in parallel. `T006` requires the demo interpreter contract from `T005`. `T007` requires the demo adapters and interpreter/review implementations (`T004`-`T006`). The entry point and seed command require the composition/SOP fixture (`T001`, `T007`). Tests follow their implementation dependencies.
- **User Story 2 (Phase 4)**: Depends on the runnable worker and composition from User Story 1. `T012` and `T013` can proceed in parallel; runtime lifecycle work `T014` requires both and extends `T008`. Tests follow runtime/repository changes.
- **Polish (Phase 5)**: Documentation tasks `T017` and `T018` can proceed in parallel after the final commands/behavior are stable. `T019` depends on all implementation and documentation tasks.

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Setup and Foundational phases; delivers the minimum safe event-processing demonstration.
- **User Story 2 (P2)**: Depends on User Story 1 because it adds lifecycle, recovery, and operator handling to the executable entry point.

### Within Each User Story

- Complete the demo adapter/interpreter contract before composition.
- Complete composition before the runnable entry point and event-to-decision integration test.
- Complete worker settings and repository recovery before adding signal handling and bounded retry behavior.
- Run focused tests after their associated implementation tasks.

## Parallel Opportunities

- **Setup**: T001 can be prepared while foundational schema/model tasks proceed.
- **Foundational**: T002 and T003 touch independent migration and Python source files.
- **User Story 1**: T004 and T005 can be implemented in parallel; T010 can be authored alongside implementation once the intended contracts are stable.
- **User Story 2**: T012 and T013 can be implemented in parallel; settings tests and repository tests can be developed independently after their respective interfaces are agreed.
- **Polish**: T017 and T018 are independent documentation updates after implementation choices are finalized.

## Parallel Example: User Story 1

```text
Task: T004 - Implement non-delivering demo action adapters in src/adapters/demo_action_adapters.py
Task: T005 - Implement demo interpreter and review router in src/workers/worker_composition.py
```

After these complete, implement T006, then T007, followed by the entry point and seed command. T010 and T011 validate the composed story.

## Parallel Example: User Story 2

```text
Task: T012 - Add validated worker settings in src/config/settings.py
Task: T013 - Add claim timestamps and stale recovery in src/app/memory/load_timeline.py
```

After both complete, implement T014 in `src/workers/run_worker.py`; then validate with T015 and T016.

## Implementation Strategy

1. **MVP first**: Complete Setup, Foundational, and User Story 1. This produces a repeatable demonstration of persisted event processing with deterministic decisions and no external side effects.
2. **Operational safety**: Complete User Story 2 before deploying the worker as a persistent hosted process, especially stale-claim recovery and graceful shutdown.
3. **Release readiness**: Complete documentation and run the quickstart end to end against a PostgreSQL database.
4. **Explicit non-goals**: Do not enable live provider calls or real external action delivery as part of this feature; implement those only after their adapters, idempotency, and human-review workflow are separately specified.
