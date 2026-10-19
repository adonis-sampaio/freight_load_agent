<!--
Sync Impact Report
- Version change: v0.0.0 -> v1.0.0
- Modified principles: N/A (new constitution scaffold adopted)
- Added sections: Operational Safety Constraints, Delivery Workflow
- Removed sections: none
- Follow-up TODOs: none
-->

# FreightOps Agent Constitution

## Core Principles

### I. SOP-First Decisioning
The agent MUST determine the active SOP before acting on any load event. The standard procedure is the default baseline, but a client-specific override for the matching load stage and context MUST take precedence. The agent MUST treat "no action" as a valid decision when the event does not match an active rule or when a prior request has not been resolved.

This rule protects operational correctness. In freight work, a wrong action can create delays, customer dissatisfaction, and unnecessary calls. The SOP is the source of truth for both standard work and client-specific exceptions.

### II. Evidence-Backed Memory
The agent MUST keep a durable, auditable memory of the load's prior messages, open questions, timer-driven follow-ups, and prior actions. Every next action MUST be grounded in the current event plus the load history, not in isolated snapshots or assumptions.

This is required because the same load can involve multiple messages, unanswered ETA requests, and delayed follow-ups across hours or days. Reliable memory prevents duplicate prompts, stale escalations, and contradictory actions.

### III. Human-in-the-Loop Escalation
The system MUST escalate to a human whenever the SOP requires it or when the agent cannot safely proceed without additional confirmation. Internal escalations go to the Hero team, and external escalations go to the broker or carrier contact according to the active procedure.

The project exists to support operations, not replace human judgment in exception handling. Escalation is how the system stays safe when contact is missing, ambiguity remains, or a high-risk situation requires a person to act.

### IV. Traceable Actioning
Every external or internal action MUST be recorded with enough context to explain why it happened. This includes the triggering event, the relevant SOP rule, the actor involved, the resulting message or task, and the time of action.

Transparent action logs are essential for debugging production issues, verifying regulatory and client obligations, and evaluating whether the agent followed the right procedure under changing conditions.

### V. Reliability, Cost, and Scale Discipline
The agent MUST be designed for high-volume operational work: short response times, bounded LLM cost, and predictable handling of many loads and messages. The system MUST prefer deterministic orchestration, low-cost batching, and explicit fallbacks over unnecessary model calls and uncontrolled retries.

Large broker operations cannot tolerate unpredictable runtimes or runaway AI costs. Reliability and cost discipline are design constraints, not afterthoughts.

## Operational Safety Constraints

The following constraints are non-negotiable for all features and changes:

- SOP files MUST remain plain-language Markdown owned by operational stakeholders, not embedded in code or hidden in brittle logic paths.
- Client-specific procedures MUST be overrideable without rewriting the standard procedure for every broker.
- The agent MUST never act on an unrelated message as if it answered an open question. Unclear or non-applicable messages require a no-action decision unless a rule explicitly says otherwise.
- Any workflow that touches driver, broker, or carrier communications MUST validate contact identity, channel availability, and destination scope before sending an action.
- Attachments, timers, and metadata MUST be treated as structured operational inputs, not as optional extras.
- Delays, escalations, and state updates MUST be auditable and reviewable by operations teams.
- The system MUST support both standard operations and client-specific exceptions across many brokers and load types without creating one-off logic branches for every scenario.

## Development Workflow

All product and engineering work MUST follow this operating model:

- Define the current operational problem and the exact scenario in a human-readable specification before implementation.
- Model the decision path using the active SOP, load context, and message history as the primary inputs.
- Validate edge cases and no-action scenarios before shipping behavior changes, especially where a missed action could delay a load or create a false escalation.
- Keep evaluation coverage aligned with real operational sequences: high-risk delays, no-response flows, unrelated message handling, and client-specific override behavior.
- When changing SOP logic or escalation behavior, verify that standard procedures still work and that client overrides remain correctly prioritized.
- Prefer small, testable changes with clear evidence that the behavior matches the written SOP and the expected load outcome.

## Governance

This constitution governs the design, review, and evolution of the FreightOps agent and related operational workflows. It supersedes informal practices that conflict with its principles. Amendments require a documented rationale, a version bump, and review against the existing operational requirements and evaluation coverage.

The project MUST maintain a clear distinction between policy (SOPs and operational rules) and implementation (system logic that executes those policies). Governance decisions that change policy precedence, escalation safety, or evidence requirements MUST be treated as high-risk changes and require explicit validation before release.

**Version**: 1.0.0 | **Ratified**: 2026-10-17 | **Last Amended**: 2026-10-17
