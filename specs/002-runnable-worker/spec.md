# Feature Specification: Runnable Background Worker

**Feature Branch**: `[002-runnable-worker]`

**Created**: 2026-10-20

**Status**: Draft

**Input**: User description: "The project is not runnable yet. I need to make it runnable to, at least, show that it is working for other people. According to you, the things that need to be done are the following. To make it runnable, the project needs a worker entry point that: 1. Opens a connection to PostgreSQL using `FREIGHT_DATABASE_URL`. 2. Constructs the repositories, decision context builder, decision engine, and action adapters. 3. Creates `LoadWorker` and repeatedly calls `run_once()`. That method processes one queued event per call; the entry point must keep polling and handle idle periods and shutdown. Create a specification to make the project run."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Start event processing for a demonstration (Priority: P1)

An operator needs to start a separate background process that reads accepted load events, evaluates them through the existing decision flow, and records the outcome. This lets the operator demonstrate that the project does more than accept API requests.

**Why this priority**: Processing a queued event is the minimum end-to-end behavior needed to demonstrate that the agent is running.

**Independent Test**: Start the worker against a prepared demonstration database, submit an event for a load with an applicable SOP, and verify that the event reaches a terminal processing state and an auditable decision is recorded without making a real external communication.

**Acceptance Scenarios**:

1. **Given** a reachable database with a valid demonstration load and applicable SOP, **When** an operator starts the worker and submits a valid event for that load, **Then** the event is claimed, processed through the decision flow, and its outcome is persisted.
2. **Given** the worker is running with no queued events, **When** an event is later accepted, **Then** the worker discovers and processes it without requiring a restart or a manual per-event command.
3. **Given** the worker is configured for a safe demonstration, **When** a decision proposes an external action, **Then** the decision is recorded but no real message, task, or timer is sent or created.
4. **Given** a proposed action conflicts with the active SOP or deterministic safety rules, **When** the event is evaluated, **Then** the action is not accepted or delivered and the rejected or review outcome is recorded.
5. **Given** an event has no applicable SOP, **When** the worker processes it, **Then** it records an explicit no-action outcome and completes or visibly fails the event according to the existing decision flow.
6. **Given** the worker uses its deterministic demonstration interpreter, **When** it processes an event, **Then** the recorded outcome is identified as a demo decision and no billable LLM usage is reserved or reported.

### User Story 2 - Operate and stop the worker safely (Priority: P2)

An operator needs the worker to report configuration or database connection problems clearly, wait efficiently when idle, and stop predictably when asked, so it can be run during a demonstration or hosted as a separate service.

**Why this priority**: A worker that cannot be started, observed, or stopped reliably is difficult to demonstrate and operate.

**Independent Test**: Run the worker with valid and invalid database configuration, observe startup outcomes, leave it idle, and request shutdown while idle and while processing an event.

**Acceptance Scenarios**:

1. **Given** required database configuration is missing or invalid, **When** the operator starts the worker, **Then** startup fails visibly with a useful error and does not report that processing has begun.
2. **Given** no event is available, **When** the worker polls, **Then** it waits before checking again rather than continuously consuming resources in a tight loop.
3. **Given** the operator requests shutdown while an event is being processed, **When** the current processing operation finishes, **Then** the worker stops accepting more work and closes its database connection cleanly.
4. **Given** an event fails during processing, **When** the failure is recorded, **Then** the operator can identify the failed event and the worker remains available to process later events unless its database connection is unavailable.
5. **Given** a worker is interrupted after claiming an event, **When** an operator restarts or inspects the service, **Then** the event is either made available for a safe retry or clearly surfaced for operator resolution rather than remaining silently stuck.
6. **Given** multiple worker instances are running, **When** queued events are claimed, **Then** a single event is not processed concurrently by more than one worker.

### Edge Cases

- If the database is unavailable during startup, the worker reports an error and does not report readiness; if connectivity is lost while polling, it reports the failure and does not mark unprocessed work as completed.
- If an event references a missing load, the event is visibly marked as failed and the worker continues with later events when the database remains available.
- If an event fails after it is claimed, its failure is recorded and the worker does not silently report success.
- If shutdown is requested while idle, the worker stops polling and closes its database connection without claiming another event.
- If a worker is interrupted with an event in progress, the event becomes retryable or is clearly flagged for operator resolution; it does not remain silently stuck in a processing state.
- If more than one worker is running, event claiming prevents simultaneous processing of the same event.
- If a proposed external action cannot be delivered in demonstration mode, the decision remains recorded as a simulated/non-delivering outcome and is not reported as a successful real-world delivery.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide an independently startable background process for handling accepted load events, separate from the event-ingestion service.
- **FR-002**: The background process MUST require and validate the database connection configuration before reporting readiness.
- **FR-003**: The background process MUST construct and connect all data access, context-building, decision, and action-handling components required to process an event.
- **FR-004**: The background process MUST continuously check for queued events and process available events without requiring an operator to invoke processing separately for each event.
- **FR-005**: The background process MUST pause for a configurable, bounded interval when no event is available.
- **FR-006**: The background process MUST record successful processing as completed and expose processing failures as failures rather than reporting them as successful decisions; an event-level processing failure MUST NOT stop later events from being processed while the database remains available.
- **FR-007**: The background process MUST handle an event with no applicable SOP through the system's explicit no-action outcome.
- **FR-008**: The system MUST provide a safe demonstration mode in which decision outcomes can be exercised and recorded without sending real messages, creating real external tasks, or scheduling real external timers.
- **FR-009**: The system MUST communicate startup, readiness, processing failure, and shutdown status through operator-accessible logs or output.
- **FR-010**: On a graceful shutdown request, the background process MUST finish its current event, stop claiming further events, and close its database resources.
- **FR-011**: The system MUST make events recoverable after a worker interruption by making them retryable or clearly surfacing them for operator resolution, without silently leaving them permanently unavailable for processing.
- **FR-012**: The system MUST allow an operator to prepare or identify the load and applicable SOP data needed for a repeatable demonstration.
- **FR-013**: The decision process MUST continue to validate any proposed action against the applicable SOP and deterministic safety rules before it is recorded as accepted.
- **FR-014**: Demonstration decisions MUST be identified as deterministic demo outcomes and MUST NOT reserve or report billable LLM usage.

### Key Entities

- **Load event**: An inbound operational update associated with a broker and load, with a processing state and a recorded outcome.
- **Load and SOP context**: The load facts, recent event history, contacts, and active procedure used to evaluate an event.
- **Decision outcome**: The auditable result of evaluating an event, including an accepted proposal, no-action result, or review/failure outcome.
- **Worker run**: A period during which the background process is configured, connected, consuming events, and eventually shut down.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can start the background process using documented project instructions and receive an explicit readiness or configuration error within 30 seconds.
- **SC-002**: In a prepared demonstration environment, 10 consecutive valid events are each processed to a visible terminal state and have an associated recorded decision.
- **SC-003**: While the worker is idle, a newly queued event is discovered and processed within 10 seconds without restarting the worker.
- **SC-004**: In demonstration mode, 100% of proposed external communications, tasks, and timers remain non-delivering while their decision outcomes are recorded.
- **SC-005**: A graceful shutdown completes within 30 seconds after the current event finishes and releases the worker's database connection.
- **SC-006**: An operator can distinguish a completed event, a no-applicable-SOP outcome, and a processing failure from the persisted event and decision records or worker logs.
- **SC-007**: Across 10 demonstration events, the records identify deterministic demo decisions and show no billable provider usage reserved or reported.

## Assumptions

- The API and worker use the same PostgreSQL database, but are operated as separate processes.
- A demonstration database can be prepared with at least one valid load and an active SOP rule and document matching the event.
- The first runnable milestone prioritizes a safe repeatable demonstration using a clearly identified deterministic no-action interpreter; it does not require a live LLM provider or real external messaging integrations.
- Demonstration mode must not reserve or report billable LLM usage; tests may inject valid action proposals to verify that non-delivering demo adapters prevent external effects.
- The existing deterministic validation and no-action behavior remain authoritative; the worker does not bypass them.
- The operator can configure the worker's idle polling interval and choose the safe demonstration behavior through documented configuration.
- Hosting a continuously running worker requires a platform service type that supports background processes.
