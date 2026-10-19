# Tasks: Freight Load SOP Monitoring Agent

**Input**: Design documents from `/specs/001-load-sop-agent/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Included because the feature specification defines independent user-story tests and acceptance scenarios.

**Organization**: Tasks are grouped by user story. Complete Setup and Foundational first; each story then has an independent validation target.

## Phase 1: Setup

**Purpose**: Establish the Python service scaffold and shared tooling.

- [X] T001 [P] Create `pyproject.toml` for Python 3.11+ with FastAPI, Uvicorn, Pydantic, asyncpg, httpx, python-dateutil, structlog, and pytest dependencies and pytest configuration.
- [X] T002 [P] Create the package and test directory skeleton in `src/app/`, `src/adapters/`, `src/models/`, `src/workers/`, `src/config/`, `tests/unit/`, `tests/integration/`, and `tests/contract/`, including package `__init__.py` files.
- [X] T003 [P] Add environment-based settings and structured logging in `src/config/settings.py` and `src/config/logging.py`; read the LLM API key from secret configuration, never hard-code it, and redact it from logs.

---

## Phase 2: Foundational

**Purpose**: Build persistence, event intake, and provider/action boundaries used by all user stories.

- [X] T004 Create the initial PostgreSQL schema and migration in `migrations/001_initial_schema.sql` for Load, Stop, Contact, LoadEvent, OpenRequest, LoadAction, DecisionRecord, SOPRule, SOPDocument, and DocumentAttachment, preserving the enums and validation rules in `data-model.md`, including “`load_id` must be unique within the broker context,” “`current_stage` must be one of the defined lifecycle states,” and “`sequence` must be unique per load.”
- [X] T005 Implement the async PostgreSQL connection and durable repositories in `src/app/memory/database.py`, `src/app/memory/load_timeline.py`, `src/app/memory/request_store.py`, `src/app/memory/action_ledger.py`, and `src/app/memory/sop_repository.py`; persist events/actions with correlation identifiers and provide a standard-SOP-only lookup path for the MVP.
- [X] T006 [P] Define validated Pydantic records for Load, Stop, Contact, LoadEvent, OpenRequest, LoadAction, SOPRule, SOPDocument, and DocumentAttachment in `src/models/load.py`, `src/models/event.py`, `src/models/action.py`, `src/models/contact.py`, `src/models/sop.py`, and `src/models/document.py`; preserve `current_stage`: dispatched, to_pickup, at_pickup, to_delivery, at_delivery, delivered; `delay_risk`: low, medium, high; and the exact rules “`delay_risk` must be recalculated or updated after each location ping,” “`sequence` must be unique per load,” “pickup and delivery stops must maintain a valid order along the route,” “`contact_id` must map to a valid external address in the message service layer,” “A driver may have multiple channels, but only one primary operational channel for a given step,” “`payload_json` must be parseable for known event types,” “timer events must correlate to an existing open request or scheduled action,” “`state` must transition only through defined lifecycle states,” and “An unanswered request can be reopened only if the previous request remains relevant and the SOP requires it.”
- [X] T007 [P] Define the normalized LLM proposal schema and provider adapter protocol in `src/models/decision.py` and `src/adapters/llm_provider_adapter.py`; support model `model_disposition` values `propose_action`, `no_action`, and `abstain`, require empty `proposed_actions` for model `no_action` and `abstain`, and keep provider/system failure separate from the persisted system decision.
- [X] T008 Implement FastAPI application setup and the `POST /v1/events/ingest` contract in `src/app/api.py` and `src/app/agent/event_router.py`; validate the request, persist a LoadEvent, and return the accepted event ID and queued status from `contracts/internal-integration-contracts.md`.
- [X] T009 Implement explicit provider error types, bounded timeout/retry policy, and safe structured error logging in `src/adapters/llm_provider_adapter.py` and `src/config/settings.py`; retry only transient failures, and route quota, billing, or configured-budget failures to the degraded review path.

**Checkpoint**: PostgreSQL persistence, validated domain records, event ingestion, settings, and mockable provider interfaces are ready for story implementation.

---

## Phase 3: User Story 1 - High-delay-risk load follow-up (Priority: P1) 🎯 MVP

**Goal**: Interpret the active standard SOP with the LLM, request an ETA, preserve request state across replies/timers, and safely no-op or escalate when required.

**Independent Test**: Run a pickup load from low-to-high delay risk through the ETA request, a driver clarification, two timer expirations, and final escalation; verify approved actions, open-request state, and audit records. An unrelated message must produce explicit no-action and no external action.

### Tests for User Story 1

- [X] T010 [P] [US1] Add the high-delay-risk, clarification, timer, and final-escalation integration sequence in `tests/integration/test_delay_risk_flow.py` using a standard SOP fixture at `tests/fixtures/sops/standard.md`; assert each outbound action is traceable to its triggering event and SOP rule.
- [X] T011 [P] [US1] Add unclear/unrelated-message and invalid/unavailable-provider cases in `tests/integration/test_no_action_flow.py`, asserting every unclear/non-applicable event persists system `disposition=no_action`, model abstention is captured separately with review status when present, provider failures remain distinct, and no unapproved action is sent.
- [X] T012 [P] [US1] Add normalized proposal schema and provider-adapter contract tests in `tests/contract/test_llm_decision_contract.py`, covering schema violations, stale SOP citations, unsupported actions, transient errors, and permanent quota/billing errors.

### Implementation for User Story 1

- [X] T013 [US1] Implement immutable DecisionRecord validation and persistence in `src/models/decision.py` and `src/app/memory/decision_store.py`, including `sop_version`, `sop_content_hash`, `ruleset_version`, `provider_id`, `model_id`, `provider_request_id`, `prompt_schema_version`, `validation_reasons`, and the rule that provider credentials and secret values are never stored.
- [X] T014 [US1] Implement decision-context construction in `src/app/agent/decision_context.py` from the triggering event, recent load timeline, available load facts, open requests, and standard-SOP-only lookup in `src/app/memory/sop_repository.py`; include only the minimum load context needed for the decision.
- [X] T015 [US1] Implement the LLM SOP interpreter and provider request/response normalization in `src/app/services/llm_sop_interpreter.py`; send decision-relevant context only and accept only the schema from `contracts/internal-integration-contracts.md`.
- [X] T016 [US1] Implement deterministic proposal validation in `src/app/rules/decision_validator.py`; verify active SOP identity/hash and exact supporting passage, allowed action and payload, cited load facts, current request semantics, contact/channel scope, and current state; persist `disposition=no_action` for unclear/unrelated/non-applicable events and separately record model abstention as review metadata that cannot authorize execution.
- [X] T017 [US1] Implement ETA OpenRequest creation, resolution, expiration, and reminder-count transitions in `src/app/memory/request_store.py`; preserve “`state` must transition only through defined lifecycle states” and “An unanswered request can be reopened only if the previous request remains relevant and the SOP requires it”; close only on a usable ETA or explicit answer to the ETA question, otherwise keep the request open until timer expiry.
- [X] T018 [US1] Implement mockable SMS, email, Hero-task, and timer adapters in `src/adapters/sms_adapter.py`, `src/adapters/email_adapter.py`, `src/adapters/task_adapter.py`, and `src/adapters/timer_adapter.py` using the documented integration contracts.
- [X] T019 [US1] Implement validated action execution and action-ledger updates in `src/app/services/action_executor.py`; preserve “`action_type` must match a permitted action catalog” and “No-action decisions must still be logged as an explicit action state”; execute only accepted proposals, validate contact identity/channel/destination before sending, and record adapter failures and correlation IDs explicitly.
- [X] T020 [US1] Wire high-risk event processing, explicit no-action logging for unclear/unrelated/non-applicable events, separate review routing, bounded LLM use, follow-up scheduling, and final Hero/dispatcher escalation in `src/app/agent/decision_engine.py` and `src/workers/load_worker.py`; re-check request state before acting on each timer event.

**Checkpoint**: User Story 1 is independently runnable using mock providers/adapters and passes the P1 delay-risk and no-action scenarios.

---

## Phase 4: User Story 2 - Client-specific SOP selection (Priority: P2)

**Goal**: Select versioned standard, client, and load-type SOP content correctly, with matching client-specific procedures taking precedence.

**Independent Test**: For identical events, verify the standard SOP applies without an override, a matching client override replaces the standard action, a client broker-alert override suppresses dispatcher email, and a refrigerated-load override requests temperature and seal data.

### Tests for User Story 2

- [X] T021 [P] [US2] Add standard fallback and client-override precedence cases in `tests/integration/test_client_override_flow.py`, verifying the active SOP identity/version reaches the decision record and only the selected SOP action executes.
- [X] T022 [P] [US2] Add SOP parsing, effective-version, and load-type override contract tests in `tests/contract/test_sop_rules.py`, including conflicting standard/client rules and missing matching sections.

### Implementation for User Story 2

- [X] T023 [US2] Implement Markdown SOP document loading and version/hash persistence in `src/app/rules/sop_loader.py` and `src/app/memory/sop_repository.py`; retain `content_markdown`, `document_id`, `client_id`, `chapter_name`, and versioned content for provenance checks.
- [X] T024 [US2] Implement SOPRule indexing and validation in `src/models/sop.py` and `src/app/rules/sop_loader.py`; preserve the scopes `standard`, `client_override`, and `load_type_override`, applicable stage/event types, priority, version, and effective dates, and enforce “applicability and precedence are resolved before model interpretation” and “changes must be versioned and auditable.”
- [X] T025 [US2] Implement deterministic active-SOP selection in `src/app/rules/rule_selector.py`; apply exact client/load-type override for matching stage first, then client default, then standard fallback, and reject expired or ambiguous selections rather than silently choosing one.
- [X] T026 [US2] Pass the selected SOP sections, immutable content hash, version, and precedence result into `src/app/agent/decision_context.py`, and verify proposal citations against that exact selected source in `src/app/rules/decision_validator.py`.
- [X] T027 [US2] Add client-specific broker-alert and refrigerated temperature/seal action handling through `src/app/services/action_executor.py` and `src/app/agent/decision_engine.py`, ensuring a selected broker-alert override does not also send the standard dispatcher email.

**Checkpoint**: User Story 2 produces distinct, auditable decisions for standard and override loads without client-specific application branches.

---

## Phase 5: User Story 3 - Load-state monitoring and document-based decisions (Priority: P3)

**Goal**: Maintain load/stop lifecycle state and use ping, metadata, attachment, and timer events as structured inputs to the selected SOP decision.

**Independent Test**: Process a multi-stop load through pickup and delivery with location pings, a classified/extracted attachment, and a timer expiration; verify stage, document values, open task context, and action history are consistent.

### Tests for User Story 3

- [X] T028 [P] [US3] Add multi-stop lifecycle and ping-driven stage/risk cases in `tests/integration/test_load_lifecycle_flow.py`, asserting valid stage transitions and per-stop remaining actions.
- [X] T029 [P] [US3] Add BOL, POD, rate-confirmation, receipt, unknown-document, and extraction validation cases in `tests/integration/test_document_flow.py`, asserting document type is identified before extracted values are accepted.
- [X] T030 [P] [US3] Add timer-resumption and stale-reminder cases in `tests/integration/test_timer_resume_flow.py`, asserting the event resumes its originating open request with prior context and never executes after that request is resolved.

### Implementation for User Story 3

- [X] T031 [US3] Implement Stop records and guarded multi-stop stage transitions in `src/models/load.py` and `src/app/agent/stage_tracker.py`; preserve the lifecycle `dispatched -> to_pickup -> at_pickup -> to_delivery -> at_delivery -> delivered` and enforce “`sequence` must be unique per load” and “pickup and delivery stops must maintain a valid order along the route.”
- [X] T032 [US3] Extend event routing and load-state updates for ping, metadata_update, and stage_change events in `src/app/agent/event_router.py` and `src/app/agent/load_runner.py`; enforce “`delay_risk` must be recalculated or updated after each location ping” and persist source timestamps.
- [X] T033 [US3] Implement attachment ingestion, content/hash storage, and status transitions in `src/models/document.py` and `src/app/services/document_classifier.py`; enforce “`document_type` must be recognized before field extraction is accepted” and ensure “extracted values must be versioned and reviewable for audits.”
- [X] T034 [US3] Implement document-type classification followed by key-value extraction in `src/app/services/document_classifier.py` and `src/app/services/document_extractor.py`; support BOL, POD, rate confirmation, receipt, and unknown types while retaining reviewable extracted values.
- [X] T035 [US3] Implement timer-event correlation and resumed decision-context reconstruction in `src/app/agent/load_runner.py` and `src/app/agent/decision_context.py`; verify the associated request is still open and reload the originating event and relevant message history before processing.

**Checkpoint**: User Story 3 independently processes full-load lifecycle and document/timer inputs while retaining auditable context.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Validate operational quality, safety, and documented run scenarios across all stories.

- [X] T036 [P] Add representative scenario evaluation fixtures and outcome metrics in `tests/evals/`; cover required and no-action decisions, SOP citations, abstentions, action correctness, and false-positive rate against the success criteria in `spec.md`.
- [X] T037 Add bounded input/output tokens, per-load inference call/spend limits, and latency/cost metrics in `src/config/settings.py`, `src/app/services/llm_sop_interpreter.py`, and `src/app/agent/decision_engine.py`; keep the 5-minute run target and approximately $1-per-load target observable.
- [X] T038 Add safe degraded handling for missing contacts/channels, failed email-thread updates, and unavailable action adapters in `src/app/services/action_executor.py` and `src/app/agent/decision_engine.py`; preserve failure details and route unresolved cases to human review without silent fallback.
- [X] T039 Update `specs/001-load-sop-agent/quickstart.md` with concrete local setup, mock-provider configuration, and runnable commands for the implemented service and tests, preserving the expected outcomes.
- [X] T040 Run the full quickstart validation and all relevant unit, contract, integration, and evaluation tests; record any remaining unmet success criteria in `specs/001-load-sop-agent/quickstart.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; T001, T002, and T003 can run in parallel because they update independent files.
- **Foundational (Phase 2)**: Depends on Setup. T004, T006, and T007 can be implemented in parallel after T001/T002; T005 depends on T004 and T006; T008 depends on T003, T005, and T006; T009 depends on T003 and T007. All foundational work blocks user stories.
- **User Stories (Phases 3-5)**: Begin after Phase 2. Stories are designed to be independently testable; they can proceed in parallel once their shared foundation is complete.
- **Polish (Phase 6)**: Depends on the desired user stories being implemented.

### User Story Dependencies

- **User Story 1 (P1)**: Depends on Foundational work only. Delivers the MVP standard-SOP delay-risk path.
- **User Story 2 (P2)**: Depends on Foundational work only. It extends SOP selection and is independently testable with standard and override fixtures.
- **User Story 3 (P3)**: Depends on Foundational work only. It extends event kinds and document/lifecycle handling.

### Within Each User Story

- Implement the listed tests first and verify they fail before implementing the behavior.
- Complete domain/schema work before services, and services before wiring orchestration.
- Run the independent test criteria at the story checkpoint before moving on.

### Parallel Opportunities

- Setup: T001 and T002.
- Foundational: T004, T006, and T007 after package initialization; T009 can proceed independently of persistence once settings and provider interface are ready.
- US1 tests T010-T012 can be authored independently; adapter implementations T018 can proceed separately from the decision-context and validator work.
- US2 tests T021-T022 can be authored independently; Markdown loading and precedence selection can proceed on separate files once models are defined.
- US3 tests T028-T030 can be authored independently; stage tracking and document classification/extraction can proceed in parallel on separate modules.
- After Phase 2, US1, US2, and US3 can be assigned to separate contributors, with shared-file integrations coordinated at their checkpoints.

## Parallel Example: User Story 1

```text
Task: "Add the high-delay-risk and clarification integration sequence in tests/integration/test_delay_risk_flow.py"
Task: "Add no-action and invalid-provider integration cases in tests/integration/test_no_action_flow.py"
Task: "Add normalized LLM proposal contract cases in tests/contract/test_llm_decision_contract.py"
```

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Complete Setup and Foundational phases.
2. Implement User Story 1 and its P1 acceptance scenarios.
3. Verify the entire delay-risk, request, timer, escalation, no-action with separate abstention/review metadata, and provider-failure path using mocks.
4. Stop for independent review before adding client overrides or document lifecycle scope.

### Incremental Delivery

1. Complete Setup + Foundational → event persistence, service configuration, and provider/action boundaries ready.
2. Add User Story 1 → demonstrate core operational follow-up and safe no-action.
3. Add User Story 2 → demonstrate SOP override precedence without client-specific code branches.
4. Add User Story 3 → demonstrate multi-stop lifecycle and document/timer processing.
5. Complete cross-cutting evaluation and quickstart validation.

## Notes

- Every task uses the required checkbox, sequential task ID, applicable `[P]` marker, required user-story label, and an explicit file path.
- `[P]` indicates independent work in separate files after stated prerequisites are complete.
- LLM output is a proposal only; action adapters are called only after deterministic validation.
- The configured provider vendor/model remains a deployment choice; the provider adapter must conform to the normalized contract without assuming cross-provider failover.
