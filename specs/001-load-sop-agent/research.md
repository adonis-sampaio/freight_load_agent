# Research: Freight Load SOP Monitoring Agent

## Decision: Event-driven orchestration with an LLM-assisted SOP decision engine

The system will use a per-load event loop that ingests messages, pings, timers, and attachments, resolves the active SOP, and selects the next valid action. For decision-relevant events, the configured LLM will interpret the applicable plain-language SOP together with the event and load history, then return a schema-constrained proposal. Unclear, unrelated, or non-applicable events always create an explicit no-action decision. The model may additionally signal abstention; this is recorded as review metadata and routed for human review when needed, never substituted for the required no-action decision or treated as permission to act. Proposed follow-up or escalation actions are represented by catalogued action types. A deterministic validator will check the cited SOP/version and evidence, action catalog, client override precedence, request state, contact/channel scope, and operational invariants before any action can execute. A missing, malformed, unsupported, or uncertain proposal fails closed and is recorded for review rather than silently converted into an action.

### Rationale

- The workload is operational and highly event-driven, so an event loop is a better fit than a single end-to-end agent run.
- The SOP is a policy layer that changes often and by client, so its plain-language instructions and precedence should be interpreted from versioned source documents rather than translated into one-off application code for each update.
- An LLM is useful for matching varied, unstructured messages and operational context to those instructions, but its proposal is not trusted as authority to send or mutate state.
- Deterministic event routing and validation, selective calls, strict schemas, retry/spend caps, and explicit abstention bound cost and preserve no-action and safety behavior.

### Alternatives considered

1. Unrestricted LLM execution on every event
   - Pros: simple initial prototype.
   - Cons: unpredictable cost and latency, weaker operational guarantees, and unsafe if free-form model output can directly send messages or change state.

2. Deterministic rules as the sole SOP interpreter
   - Pros: predictable, testable, and straightforward to audit.
   - Cons: brittle for plain-language SOPs and noisy messages; SOP changes can require ongoing rule translation and maintenance.

3. LLM decision proposals behind deterministic guards (chosen)
   - Pros: uses the provided LLM for language understanding and SOP variation while keeping execution, state transitions, precedence, and safety checks deterministic.
   - Cons: decisions remain probabilistic, require representative scenario evaluations and evidence review, and must account for provider availability, latency, and token cost.

The provided API key enables one configured provider; it does not establish that one provider is sufficient for availability or that the system should depend directly on its SDK. Keep a narrow provider adapter and configuration seam, but do not build multi-provider failover until an additional credential/provider is available and the operational requirement justifies it.

## Decision: Standard SOP is baseline, client override wins for matching stage

The active SOP for a load will be selected as: exact client override for the current stage, then client default, then standard SOP. If multiple rules match, the most specific rule wins.

### Rationale

- The specification explicitly requires client-specific rules to override the standard procedure when they apply to the same event.
- This preserves a baseline while allowing operational flexibility for different brokers and load types.
- It also makes the decision path explainable and auditable.

## Decision: Load timeline and action ledger are the primary memory model

The agent will keep an append-only timeline for each load: event history, action history, open requests, active timers, and current stage. Pending questions remain in a structured task table until resolved or expired.

### Rationale

- The example sequence requires the system to remember that the ETA request is still open after a driver asks a clarifying question.
- A timeline-based memory model makes retries, auditability, and stale-message filtering straightforward.
- It supports "do nothing" decisions when a message is irrelevant to an unresolved request.

## Decision: Message-to-action proposals are constrained and validated

The model may return a constrained proposal or an abstain signal when policy/context is ambiguous or incomplete. The system records every unclear, unrelated, or non-applicable event as `no_action`; if abstention requires human review, record that signal in separate review metadata/status linked to the no-action decision. Provider/system failure is not a model disposition and is recorded separately. Every actionable proposal includes an active SOP document/version and section reference, an exact supporting passage, relevant load-fact references, and only catalogued action types. Deterministic checks validate the schema, citations, precedence, operational state, contact/channel, and action payload; invalid proposals are rejected and surfaced for human review. Persist the proposal and validation result, including immutable SOP identity/hash, ruleset and prompt/schema versions, provider/model/request identifiers, but never the API key.

### Rationale

- No-action is an explicit, auditable decision for unclear, unrelated, or non-applicable events, not a missing response or a success-shaped fallback.
- Abstention means the model cannot safely decide from the current policy and facts; it is captured as review metadata alongside the no-action decision, not as a replacement decision. Provider/system failure likewise has a distinct operational status and must not create a stale action.
- The model cannot invent an executable action because the deterministic layer accepts only known action types and validates the operational context before execution.
- Only events that can affect an active SOP decision are sent to the model; irrelevant event classes and duplicate/stale inputs are filtered by orchestration.

## Decision: Documents are classified before full extraction

Every attachment will be routed through a document classifier that identifies whether it is a BOL, POD, rate confirmation, receipt, or unknown. Only then will the system extract the values needed by the SOP.

### Rationale

- The requirement requires understanding the attachment type before key-value extraction.
- Classification first reduces wasted extraction work and keeps the document pipeline auditable and testable.

## Decision: External integrations are adapter-based and mockable

Email, SMS, Slack, TMS, and timer infrastructure will be wrapped behind adapter interfaces. Each adapter exposes a small contract for send, fetch, create timer, and status update operations.

### Rationale

- The project is explicitly described as using internal services and mocks.
- Adapters isolate third-party behavior and make unit and integration tests straightforward.
- This supports future replacement or queue-based backends without changing the core agent logic.

## Decision: Response time and cost are controlled via bounded event windows and selective prompting

Each load run will process only the relevant events for that load, and the LLM will be called only when an event may change a decision under the active SOP. The agent will batch message processing and enforce timeouts, bounded retries, input/output limits, and per-load call/token or spend budgets. Retry only transient failures; quota, billing, or hard-budget failures use an explicit degraded human-review path and never trigger external actions.

### Rationale

- The requirement calls for 5-minute runs and an approximate $1 per-load cost target.
- The design uses the LLM for the core language-dependent decision proposal while keeping event routing, policy precedence, validation, state changes, and action execution deterministic.

## Open design items resolved in this plan

- Client-specific override precedence: resolved by the rule precedence model described above.
- Persistent memory representation: resolved by the append-only load timeline and open-task table.
- Integration boundaries: resolved by mockable adapter interfaces.
- Decision safety: resolved by explicit no-action records for unclear/non-applicable events, separate human-review metadata, evidence-cited proposals, and deterministic validation.
- Performance constraints: resolved by deterministic event routing, selective LLM use, bounded retries, and per-load inference budgets.
- Provider choice: the design requires one configured provider credential; the provider/model must be set in deployment configuration, with no multi-provider failover assumed.

## Provider documentation references

The implementation must verify the equivalents for the actual configured vendor and model; these references are examples, not a provider selection:

- [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs) distinguishes schema-constrained output from semantic correctness.
- [OpenAI Evals](https://developers.openai.com/api/docs/guides/evals.md) supports task-specific evaluation over representative cases.
- [OpenAI Production Best Practices](https://developers.openai.com/api/docs/guides/production-best-practices.md), [Rate Limits](https://developers.openai.com/api/docs/guides/rate-limits.md), [Spend Limits](https://developers.openai.com/api/docs/guides/spend-limits.md), and [Error Codes](https://developers.openai.com/api/docs/guides/error-codes.md) describe server-side key management, bounded rate-aware behavior, spend controls, and distinct provider failure handling.
- [NIST AI Risk Management Framework](https://www.nist.gov/itl/ai-risk-management-framework) provides broader governance context for model risk and oversight.
