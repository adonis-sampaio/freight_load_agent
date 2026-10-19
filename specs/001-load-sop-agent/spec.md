# Feature Specification: Load SOP Monitoring Agent

**Feature Branch**: `[001-load-sop-agent]`

**Created**: 2026-10-17

**Status**: Draft

**Input**: User description: "The system helps freight brokers in the US truckload market. It monitors each load from before the pickup to the last delivery. Messages are the most important signal..."

## Clarifications

### Session 2026-10-17

- Q: Which SOP precedence model should apply when a standard rule and a client-specific rule both match the same event? → A: Client-specific SOP wins for the matching load stage, and the standard SOP remains the fallback when no client-specific rule exists.
- Q: When should the system treat an outstanding ETA request as resolved? → A: Only a usable ETA or an explicit answer to the ETA question closes the request; otherwise the request stays open until the timer expires.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - High-delay-risk load follow-up (Priority: P1)

When a load enters a high-delay-risk condition, the agent must evaluate the active SOP, ask the driver for the missing ETA, track whether the request is still open, and escalate to the right channel if the driver does not respond within the configured window.

**Why this priority**: This is the core operational risk for each load. Missing the correct follow-up can delay pickups, trigger customer escalation, and create expensive service failures.

**Independent Test**: A load at pickup with a rising delay risk can be run through the sequence without any manual intervention, and the agent will create the next required action at each step.

**Acceptance Scenarios**:

1. **Given** a pickup load with low risk that changes to high risk, **When** the delay-risk event arrives, **Then** the system sends an SMS to the driver asking for the ETA and records the request as open.
2. **Given** the driver replies with a question instead of an ETA, **When** the message is processed, **Then** the system answers the question using the available load metadata, keeps the ETA request open, and does not take an unrelated action because the question does not satisfy the active ETA request.
3. **Given** the driver does not answer after the first request, **When** the timer expires, **Then** the system sends a second SMS asking for the ETA.
4. **Given** the driver still does not answer after two requests, **When** the final timer expires, **Then** the system emails the dispatcher in the main thread and creates a Hero task to call the driver.
5. **Given** the agent received a message that was not requested by the SOP, **When** that message is analyzed, **Then** the system recognizes there is no required action and takes no action.

---

### User Story 2 - Client-specific SOP selection (Priority: P2)

Different brokers and load types can require different rules even while sharing a common baseline. The agent must apply the correct standard procedure, then override it with the active client or load-specific SOP when needed.

**Why this priority**: The platform serves many clients and each client can have its own procedure. A single shared policy is not enough to satisfy operational needs or maintain trust with each broker.

**Independent Test**: A standard load and a client-specific override can run through the same decision path and produce different valid actions while still following the correct SOP.

**Acceptance Scenarios**:

1. **Given** a standard load with no override, **When** a delay-risk event occurs, **Then** the agent follows the default standard procedure for that stage.
2. **Given** Client A defines a different escalation path from the default standard, **When** the same event occurs for that client, **Then** the agent selects the client-specific SOP instead of the standard rule because the client override takes precedence for the matching stage.
3. **Given** Client B defines a broker alert instead of a dispatcher email, **When** a delay-risk event triggers the same condition, **Then** the agent sends the broker alert and does not send a dispatcher email.
4. **Given** a refrigerated load with a client-specific rule for trailer temperature and seal collection, **When** the driver leaves pickup, **Then** the system requests the required trailer temperature and seal number before proceeding with the next stage.

---

### User Story 3 - Load-state monitoring and document-based decisions (Priority: P3)

The agent must monitor the full lifecycle of a load, update stage and status consistently, and interpret message, document, and timer inputs that affect the next action.

**Why this priority**: Freight operations depend on accurate state changes, document identification, and timely escalation. This gives operators confidence that the load is being managed correctly across the full journey.

**Independent Test**: A multi-stop load with attachments, pings, and timing events can be processed end-to-end, and the agent updates both the operational state and the appropriate tasks.

**Acceptance Scenarios**:

1. **Given** a load with multiple stops, **When** the load advances through pickup and delivery stages, **Then** the agent updates the current stage and remaining actions for the relevant stop.
2. **Given** a BOL, POD, rate confirmation, or receipt is attached to the load, **When** the document is reviewed, **Then** the system identifies the document type and extracts the required key values for operational use.
3. **Given** a timer created by the agent expires, **When** the timer event is received, **Then** the agent resumes the open task using the same context and prior messages that created the original request.

### Edge Cases

- What happens when message threads are noisy or a driver sends an unrelated message such as "ok" after a previously opened request?
- How does the system handle a missing or incomplete load metadata record when the agent needs a PO number, stop time, or contact ID?
- What happens when a client changes its SOP during the active life of a load or when a load type has an override that conflicts with the standard procedure?
- How does the agent avoid acting on stale reminders when a new message has already resolved the outstanding question?
- What should the system do when a required external action is not possible because a contact channel is unavailable or an email thread cannot be updated?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST ingest and organize all load events from messages, location pings, timers, attachments, and metadata into a single operational view for each load.
- **FR-002**: The system MUST maintain the current stage of each load and update it as the load progresses across pickup and delivery events.
- **FR-003**: The system MUST determine the applicable SOP for a given client, load type, and current stage, using the standard procedure as the default and client-specific overrides when defined, with the client-specific procedure taking precedence for the matching stage and the standard procedure used as the fallback.
- **FR-004**: The system MUST evaluate the latest event, prior messages, prior actions, and the current SOP to select the next valid action, including determining whether an outstanding ETA request is still open or has been resolved by a valid ETA answer.
- **FR-005**: The system MUST send or schedule external actions such as SMS, email, Slack messages, TMS notes, and follow-up timers when the SOP requires them.
- **FR-006**: The system MUST create internal tasks, update operational status, and send internal notifications when the SOP requires escalation to the Hero team or internal staff.
- **FR-007**: The system MUST preserve memory of pending questions and unresolved requests so the agent can continue the same conversation across multiple messages and timer-driven follow-ups.
- **FR-008**: The system MUST record an explicit no-action decision when a message is unclear or unrelated, does not satisfy an outstanding question, or does not match an active rule. If human review is needed to resolve the uncertainty, the system MUST record and route that review separately without taking an external action.
- **FR-009**: The system MUST identify attachments, determine the document type, and extract the key operational values required by the SOP or the load workflow.
- **FR-010**: The system MUST support multiple clients with different procedures without requiring a separate implementation for each client.
- **FR-011**: The system MUST support many loads and frequent inbound events while keeping each agent run within the operational response-time limit.
- **FR-012**: The system MUST produce an auditable record of what it did, when it acted, and why the action was selected so that operators can review and verify behavior.

### Key Entities *(include if feature involves data)*

- **Load**: The active shipment or movement, identified by a unique load ID and associated with a broker, stops, contacts, and current stage.
- **Client SOP**: The operational rule set for a broker or load type, written in plain-language instructions and organized by topic and stage.
- **Message Event**: An inbound communication from a driver, dispatcher, broker staff member, or internal system that can change the next action or required follow-up.
- **Contact**: A person or system identity that can receive SMS, email, or Slack communication, linked to the relevant channel and role.
- **Stop**: A pickup or delivery location with appointment details, geography, and sequence in the load lifecycle.
- **Timer**: A scheduled follow-up created by the agent to re-check a missing response or to trigger a later action.
- **Action**: Any external or internal operation the agent performs, including sending messages, creating tasks, or updating a status.
- **Document Attachment**: A BOL, POD, rate confirmation, receipt, or other support document that may need to be classified and parsed.
- **Escalation Task**: An internal or external task assigned to a person because the SOP requires a human to take over.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The system determines the next action for 95% of relevant load events within 5 minutes of the triggering event.
- **SC-002**: At least 90% of required follow-up messages, escalations, and task creations occur on schedule when the SOP requires them.
- **SC-003**: The system takes no action in at least 99% of cases where the event does not match an active SOP rule or where the driver sends an unrelated message after the agent has already asked a question.
- **SC-004**: At least 90% of monitored loads complete their required operational steps without a manual intervention caused by missed decision logic.
- **SC-005**: New client SOP updates can be deployed without engineering changes to the core decision engine for the standard operating flow.
- **SC-006**: The average LLM inference cost for the complete load lifecycle remains at or below approximately $1 per load across the standard operating pattern.

## Assumptions

- Client procedures are written as plain-language SOP files and are organized by topic and stage rather than being hard-coded into application logic.
- The standard procedure applies to all clients unless a client-specific override or a load-type override is activated.
- Load metadata, contact mappings, and stop information are available when the agent starts processing a load and are updated as the load changes.
- Human intervention remains required for exception handling outside the defined SOP and for calls that require direct coordination with a driver, carrier, or broker representative.
- The system must support both external communications and internal operational actions without requiring a single monolithic workflow for all clients.
- A driver does not always answer promptly, so the agent must rely on timers, memory of open questions, and escalation rules to continue the load safely.
