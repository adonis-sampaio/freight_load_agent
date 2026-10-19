# Freight Load SOP Agent: Project and Operating Design

## Purpose and scope

This document explains how the freight-load SOP agent is structured, how its
policy and memory work, and what is required to operate it safely at production
scale. It distinguishes **implemented behavior** from **production design
guidance**: the repository contains a substantial, tested domain and
orchestration core, but it is not yet a fully deployed, end-to-end service.

The agent monitors a load as an event stream. Its job is not to improvise
freight policy. It resolves the applicable Standard Operating Procedure (SOP),
uses a language model (LLM) to interpret that policy against the current event
and relevant load facts, then validates any proposed action deterministically
before execution. When the event is unrelated, the policy does not apply, or
the evidence is insufficient, the safe outcome is no external action, recorded
explicitly. Uncertainty can additionally be routed for human review.

The project’s feature requirements and design rationale are in
[`specs/001-load-sop-agent/spec.md`](../specs/001-load-sop-agent/spec.md),
[`plan.md`](../specs/001-load-sop-agent/plan.md), and
[`research.md`](../specs/001-load-sop-agent/research.md). The schema is defined
in [`migrations/001_initial_schema.sql`](../migrations/001_initial_schema.sql).

## 1. Agent architecture

### The chosen pattern: event-driven orchestration with bounded LLM proposals

The agent is an event processor, not one long-running autonomous conversation.
Messages, location pings, timer expirations, attachments, metadata updates, and
stage changes are represented as events attached to a broker and load. The
event path is:

1. **Ingest and persist.** The API validates an event, gives it an event and
   correlation identifier, and stores it in the PostgreSQL event timeline with
   a queued status. See [`event_router.py`](../src/app/agent/event_router.py)
   and [`load_timeline.py`](../src/app/memory/load_timeline.py).
2. **Apply deterministic state updates.** The worker loads the load record and
   updates guarded lifecycle state, metadata, or delay risk when the event
   provides sufficient evidence. For example, a ping can derive risk from the
   estimated arrival and the target stop’s appointment. See
   [`load_worker.py`](../src/workers/load_worker.py),
   [`load_runner.py`](../src/app/agent/load_runner.py), and
   [`stage_tracker.py`](../src/app/agent/stage_tracker.py).
3. **Resolve policy and build a small decision context.** The context builder
   finds rules that match client, load type, load stage, event type, and
   effective date. It chooses one rule, retrieves the referenced Markdown
   section, and gathers the current event, a bounded recent history, selected
   load facts, and open requests. See
   [`decision_context.py`](../src/app/agent/decision_context.py) and
   [`sop_repository.py`](../src/app/memory/sop_repository.py).
4. **Ask the LLM for a proposal, not permission to execute.** The request is
   schema-constrained and includes the active SOP identity, version, content
   hash, relevant passage, and allowed action types. A provider adapter
   isolates provider-specific code. See
   [`llm_sop_interpreter.py`](../src/app/services/llm_sop_interpreter.py),
   [`llm_provider_adapter.py`](../src/adapters/llm_provider_adapter.py), and
   [`models/decision.py`](../src/models/decision.py).
5. **Validate and execute.** Deterministic code checks proposal schema,
   provenance, exact SOP passage, load evidence, action type and payload,
   contact/channel, and operational state. Invalid or uncertain proposals
   cannot authorize outbound work. Accepted actions go through adapters and
   are recorded in the action ledger; decisions and review outcomes are stored
   separately. See [`decision_validator.py`](../src/app/rules/decision_validator.py),
   [`action_executor.py`](../src/app/services/action_executor.py), and
   [`decision_store.py`](../src/app/memory/decision_store.py).

This division makes policy interpretation flexible without making the model
the system of record or the authority that sends messages. A model failure is
not treated as a successful no-op: provider/system failure, invalid proposal,
no-action decision, and human-review status remain distinguishable.

### Alternatives and trade-offs

| Approach | Strengths | Weaknesses | Why it is or is not the fit |
|---|---|---|---|
| Unrestricted LLM agent that reads everything and executes tools | Fast to prototype; handles varied language | Hard to audit, nondeterministic, expensive on every event, broad action surface, and unsafe if model output directly triggers side effects | Not used. The model proposes; deterministic validation and adapters control execution. |
| Deterministic rules only | Predictable, fast, inexpensive, and easy to unit test | Natural-language variations and client SOP changes require brittle hand-coded rules | Useful for invariants, routing, state transitions, and validation, but insufficient as the only language interpreter. |
| Event-driven service with constrained LLM proposals (chosen) | Auditable per event; state survives restarts; accommodates plain-language SOPs; LLM use can be bounded | Still needs high-quality evaluations, policy versioning, provider reliability, and explicit cost/latency limits | Best balance for operational workflow: deterministic policy selection and safety around language understanding. |
| Fine-tuned or client-specific models | May improve repeated, narrow tasks | Adds training, versioning, governance, and client-data isolation burdens; policy changes still need controlled rollout | Not needed for the current design. Consider only after measured failure patterns justify it. |

## 2. Documents: type identification and key-value extraction

### Intended pipeline

The safe document path is **receive → identify → extract → validate → make
available as evidence**:

1. Store attachment metadata and a content hash so the received artifact can be
   identified and checked for changes.
2. Obtain readable text using a format-specific parser or OCR service where
   required.
3. Classify the text as a supported type: Bill of Lading (BOL), Proof of
   Delivery (POD), rate confirmation, receipt, or unknown.
4. Extract only fields allowed for that type. For example, the BOL field set
   includes BOL number, PO number, seal number, and trailer number; a POD has a
   different allowlist.
5. Validate extracted values and provenance before allowing them to update
   load facts or participate in an action decision. Unknown, malformed, or
   unsupported documents must not silently become trusted facts; route them
   for review when operations require a decision.

The document classifier and extractor are separate components:
[`document_classifier.py`](../src/app/services/document_classifier.py) assigns
the type before [`document_extractor.py`](../src/app/services/document_extractor.py)
accepts values. A document has a content hash, status, extracted values, and
extraction version in [`document.py`](../src/models/document.py). This allows
the system to distinguish “received”, “classified”, “extracted”, and
“rejected”, rather than treating an attachment as verified data.

### What works today, and what does not

The current classifier operates on **text that is already available** and uses
document-name phrases and labels. The extractor accepts simple `Field: value`
lines, maps only known labels for the classified document type, and rejects
unknown types. The integration test demonstrates that order:
[`test_document_flow.py`](../tests/integration/test_document_flow.py).

The repository does **not** currently implement a PDF/image ingestion endpoint,
OCR, a general-purpose document parser, confidence scoring, or a complete
human-review UI. Therefore, it would be inaccurate to claim that a scanned
real-world BOL can already be ingested and reliably interpreted end to end.
Production deployment needs a selected text/OCR extraction adapter, file-size
and content-type limits, malware and access controls for stored files, field
validation, and human review for low-confidence or conflicting values. The LLM
should not be allowed to promote arbitrary extracted text into authoritative
load state without those checks.

## 3. SOP organization, lookup, and safe authoring

### Baseline and client-specific changes

Organize SOP content as a shared **standard baseline** plus narrowly scoped
client and, where needed, load-type exceptions:

```text
standard/
  pickup.md
  delivery.md
clients/
  client-123/
    pickup.md
    delivery.md
load-types/
  refrigerated.md
```

This is a conceptual content layout, not a directory scanner currently
implemented by the service. In the running data model, a versioned SOP document
is stored with a separate index of rules. A rule points to a Markdown section
and records scope, client, load type, stage, event types, priority, version,
and effective dates. See [`sop.py`](../src/models/sop.py) and
[`sop_repository.py`](../src/app/memory/sop_repository.py). The test baseline
is [`standard.md`](../tests/fixtures/sops/standard.md).

The policy principle is inheritance with override: **the standard is in force
for every client unless an applicable client rule replaces it for that case**.
When a matching client rule exists, it wins for that match; when none exists,
the standard remains the fallback. This is not a text merge in which a
client-specific paragraph is blended with a standard paragraph at runtime. One
specific active rule is selected for a decision. That avoids ambiguous
instructions such as inadvertently combining two different escalation paths.
Load-type rules can provide another scoped layer. A configured priority breaks
valid precedence ties; if equally preferred rules remain ambiguous, selection
raises an error instead of guessing.

### How the correct SOP is found

At event time, the repository filters rules by matching client, load type,
stage, event type, and effective period. The selector then applies scope
precedence and priority. The chosen rule identifies a section in the exact
Markdown version; the section and its source hash are passed into the decision
context. A decision record preserves the rule/document identifiers, version,
hash, section reference, and supporting passage. See
[`rule_selector.py`](../src/app/rules/rule_selector.py),
[`sop_loader.py`](../src/app/rules/sop_loader.py), and
[`decision_context.py`](../src/app/agent/decision_context.py).

**Advantages**

- A standard update can reach all clients that have not overridden the changed
  case.
- Exceptions are explicit, scoped, reviewable, and do not require branching
  application code for each client.
- Effective dates support controlled policy transitions; immutable document
  versions and content hashes make historical decisions reproducible.
- A single selected section limits irrelevant prompt context and improves
  explainability.
- Conflicting equally ranked rules fail visibly rather than being selected
  nondeterministically.

**Disadvantages and risks**

- Rule indexing and content must stay synchronized; a valid Markdown file
  alone is not enough if its index points to the wrong section, stage, or
  client.
- Broad client overrides can hide later standard improvements. Override
  coverage needs ownership and periodic review.
- Precedence is powerful but can surprise operators unless effective dates,
  scope, priority, and selected source are visible in audit records.
- Selecting one rule is intentionally conservative; genuinely cumulative
  policy needs to be authored as one complete, explicit rule rather than
  relying on an implicit merge.

### Making SOP changes safe for nontechnical authors

Policy authors should be able to edit readable prose, not database rows,
application code, or model prompts. Keep those responsibilities separate:

1. **Author in plain language.** Use a small set of templates with named
   sections and a consistent rule shape: trigger, required action, recipient
   and channel, timing, resolution condition, exception, and escalation.
   Write atomic rules—one operational decision per section.
2. **Keep routing metadata structured.** A reviewed catalog/sidecar maps each
   SOP section to its client scope, stage, event types, load type, priority,
   version, and effective dates. Do not ask the LLM to infer which client
   policy is authoritative from a folder of documents.
3. **Validate before publishing.** Check required headings/sections, unique
   identifiers, valid scope and dates, contact/channel references, supported
   action types, and conflicts with same-or-higher precedence rules. Preview
   the exact text and structured rule that would become active.
4. **Review and version.** Require an operational owner to approve changes.
   Publish a new immutable version with an effective time; never overwrite the
   source used by prior decisions. Keep an easy rollback by changing the active
   version/range, not by deleting history.
5. **Run evals before activation.** Replay affected scenarios and the shared
   safety suite. For material changes, stage the new version or shadow-evaluate
   it before routing live actions to it.
6. **Monitor after activation.** Track no-action, review, rejection, and action
   rates by SOP version/client, and make rollback operationally straightforward.

Current support is foundational, not a turnkey content-management workflow:
`SOPLoader` reads Markdown, hashes it, checks that indexed sections exist, and
`SOPRepository` persists a document and its rules without overwriting an
existing document version. There is no nontechnical editor, approval workflow,
automatic rule-index generator, or complete publication UI. Those are design
recommendations for safe operations, not claims about existing features.

## 4. Memory and follow-up timers

### What the agent remembers

Memory is durable structured state, not a hidden LLM conversation transcript:

- **Event timeline:** inbound and system events, timestamps, source channel,
  payload, processing status, and correlation identifier.
- **Load state:** current lifecycle stage, delay risk, stops, contacts, and
  allowlisted facts.
- **Open requests:** the question asked, request type, state, expiry, reminder
  count, target contact, and the correlation identifier of the event that
  opened the request.
- **Action ledger:** each attempted or completed external action, triggering
  event, SOP rule, payload, external reference, and failure state.
- **Decision records:** model disposition, system disposition, SOP version and
  hash, evidence, validation reasons, review status, and provider/model
  metadata (never the provider secret).

These records are stored in PostgreSQL tables defined by the initial migration.
The decision context is rebuilt when an event arrives; it includes a bounded
recent history rather than sending an unbounded conversation to the LLM. This
keeps memory auditable and gives each proposal only the necessary context.

### The 07:13 open ETA example

Suppose the driver was asked for an ETA earlier, and at 07:13 replies with a
question about the PO number rather than an ETA. The system does not infer that
the ETA request is complete merely because a reply arrived:

1. The message is persisted as a new event.
2. Context construction loads the request while its state is `open`, along
   with the recent messages and available facts such as the PO number.
3. The model may propose answering the PO question, but the decision logic
   checks whether the text is a usable ETA or an explicit answer to the ETA
   question.
4. If not, the request remains open. The answer to the unrelated question does
   not cancel the reminder.
5. A later usable ETA or explicit answer resolves it; otherwise the scheduled
   timer can resume the work.

`RequestStore.resolve_answer` enforces the request semantics, and
`DecisionEngine._resolve_usable_eta` invokes that check only for relevant
messages. A usable ETA is currently recognized through limited explicit clock,
duration, or timezone-aware ISO formats. Free-text understanding can propose an
interpretation, but an ambiguous answer should not silently close the request.
See [`request_store.py`](../src/app/memory/request_store.py) and
[`decision_engine.py`](../src/app/agent/decision_engine.py).

### Timer lifecycle and what a timer-triggered run sees

When an ETA request is created, the decision engine gives it an expiry and
correlation ID. A timer action is associated with that request ID. When the
timer fires, the timer service should enqueue a normal timer event for the
same load; the worker then reconstructs a fresh decision context.

The timer run sees:

- the timer event and its request ID;
- the current load state and current applicable SOP;
- the still-open request and its reminder count;
- recent load events, with the original event that opened the request restored
  by correlation ID when needed.

Before executing a timer-driven action, the engine rechecks that the request
remains open. A stale timer for an answered, cancelled, expired, or missing
request produces an explicit no-action result instead of sending an obsolete
message. Reminder counts are bounded; an escalation can be selected after the
allowed follow-up(s). The timer context restoration is covered in
[`test_timer_resume_flow.py`](../tests/integration/test_timer_resume_flow.py),
and the full mocked follow-up sequence is covered in
[`test_delay_risk_flow.py`](../tests/integration/test_delay_risk_flow.py).

The adapter contract and request context exist, but the repository currently
contains an in-memory timer mock and does not include a production scheduler
that publishes timer events. Production must use a durable scheduler/queue,
retry delivery safely, and ensure that duplicate timer events and action
retries are idempotent.

## 5. Evaluations and production correctness

### Layered verification

No single eval can establish production correctness. Use several layers:

1. **Unit tests** for parsing, state transitions, request lifecycle, SOP
   precedence, validators, and action payloads. These should be deterministic
   and fast.
2. **Contract tests** for normalized LLM proposals, supported actions,
   citations, provider errors, and adapter behavior.
3. **Integration tests** for multi-event operational journeys: high delay
   risk, driver clarification, timer follow-up, escalation, client overrides,
   document classification/extraction, and stale timers.
4. **Scenario evals** for expected dispositions, action correctness, evidence
   citation, false-positive actions, and review routing.
5. **Operational monitoring** in production: latency, queue age, event
   failures, provider errors, spend, action delivery/failure, stale timers,
   review volume, and decision outcomes segmented by client and SOP/model
   version. Alerts need an on-call owner and a safe degraded mode.

The repository has unit, contract, integration, and eval suites. The current
small eval fixture covers a required action, unrelated input, uncertainty, and
a stale SOP citation; see
[`scenarios.json`](../tests/evals/scenarios.json) and
[`test_sop_decision_evaluation.py`](../tests/evals/test_sop_decision_evaluation.py).
The integration flows use mock interpreters, so they verify orchestration and
safety contracts, not the live model’s statistical quality.

### When a client changes its procedure

Treat a SOP change as a policy release:

- Add scenarios that exercise the changed trigger, new required action,
  non-matching neighboring case, exception, timer/reply sequence, and
  precedence against standard and other applicable rules.
- Run the new client suite **and the entire shared regression suite**. Verify
  that other client SOPs are unchanged by scope isolation.
- Verify decision provenance: the new version/hash and expected section must
  be cited, and old decisions must still reproduce against the old immutable
  version.
- Include negative tests: no action when a rule does not match; no accidental
  standard action when an override explicitly replaces it; no use of a stale
  effective version.
- Canary or shadow-evaluate the new version before broad activation where
  possible. Compare proposed and actual outcomes, review rate, and action
  volume; roll back by restoring the previous effective version.

Avoid client-specific forks of shared test logic where a common scenario can
be parameterized. This ensures a general safety fix is exercised across client
variations.

### When the LLM changes

Pin the model/provider and prompt/schema versions in each run. Before changing
the model:

- Run the same frozen eval set against the old and new model, with the same
  SOP versions and context.
- Compare action precision/false positives, missed required actions, no-action
  accuracy, abstention/review rate, citation validity, schema validity,
  latency, and token/cost use. Look at per-client and per-scenario regressions,
  not only one aggregate score.
- Keep deterministic validators and provider contracts unchanged unless
  intentionally versioned. A model change must not weaken guards.
- Shadow-run or canary the new model without executing its proposed external
  actions; inspect divergences before switching production traffic.
- Preserve a rollback path and record enough metadata to attribute outcomes to
  the model and schema version.

The eval suite should be organized as a matrix, not one giant universal
prompt: shared safety invariants × client policy × load type/stage × event
class × outcome complexity. Keep a small smoke suite for every change, a broad
cross-client regression suite for releases, and targeted scenario suites for
complex clients. Track required-action coverage and false-positive actions
separately; never improve recall by accepting unsafe actions.

## 6. Response-time target, volume, and LLM cost

### Keeping a run within five minutes

The design target is a maximum of five minutes per event-driven run. It is
achieved by bounding the work, not by asking the model to “be fast”:

- Process one load event at a time with a bounded context; the current context
  builder fetches at most five recent timeline events.
- Apply cheap deterministic state updates and SOP matching before invoking the
  LLM.
- Send only allowlisted facts, open requests, the current event, relevant
  history, and the selected SOP section.
- Enforce provider timeouts, transient-only retries, input/output token limits,
  and per-load call/cost limits. Current defaults include a 30-second provider
  timeout, at most two retries, 12,000 input tokens, 2,048 output tokens, and
  20 calls per load.
- Bound adapter/network operations and queue retries in production; propagate
  a run deadline so retries and action execution cannot exceed the overall
  budget.
- If a hard budget or deadline is exhausted, record the degraded outcome and
  route for review. Do not turn a timeout into a success-shaped no-action or
  execute a partially validated proposal.

Current code supplies configurable LLM timeout/retries/token ceilings and
measures LLM latency, but there is no demonstrated end-to-end five-minute
production SLO or load benchmark. The five-minute cap must be enforced and
measured across database calls, provider calls, and external adapters—not just
the model request. Current worker processing is one event per `run_once`;
production worker lifecycle and composition remain deployment work.

### What changes at ten times the event/load volume

Tenfold volume should first be handled by scaling the event-processing
architecture, not by making every event call an LLM:

1. Increase queue consumers horizontally, with bounded concurrency per load
   and broker so events for the same load do not race or reorder incorrectly.
2. Ensure event claiming, action execution, timer handling, and message sends
   are idempotent. Use durable retries/dead-letter handling and an outbox or
   equivalent for transactional action delivery.
3. Measure PostgreSQL connection pool saturation, query latency, index use,
   write throughput, and queue age. Add capacity and indexes based on measured
   bottlenecks; partition/archive high-volume event and decision history when
   retention and query patterns justify it.
4. Store binary attachments in object storage, not the relational event
   database. Process OCR/document work asynchronously and separately from
   time-sensitive event decisions.
5. Apply backpressure, per-client fairness, provider rate limits, and queue
   priorities for urgent operational events. Cache immutable SOP versions and
   avoid repeating equivalent work where correctness permits.
6. Load-test mixed client complexity, timer bursts, provider throttling, and
   downstream adapter failure. Revisit worker topology and database sizing
   using p95/p99 latency and recovery time rather than average throughput.

The current schema has per-load keys and lookup indexes, and event claiming
uses `FOR UPDATE SKIP LOCKED` to allow concurrent consumers. That is a useful
base, not proof that tenfold scale is supported. Ordering, idempotency,
partitioning, production queueing, and capacity targets need explicit
implementation and tests before making a scale commitment.

### Keeping LLM spend around $1 or less per load

Use a **budget**, not an assumption about average model pricing:

- Invoke the LLM only where language interpretation can change the next
  decision; do not use it for simple state updates, duplicate events, known
  stale timers, or deterministic validations.
- Bound the number of calls and input/output tokens per load. The current
  defaults configure 20 calls/load and a $1/load maximum, and the decision
  store reserves budget before a provider call.
- Prefer a small/fast model for routine classification/decision proposals;
  reserve a stronger model or human review for genuinely complex cases.
- Keep prompts compact through structured context and one selected SOP section;
  do not resend full load histories or whole SOP libraries.
- Use deterministic parsing/rules for high-confidence structured values and
  repetitive decisions, with the LLM as a narrow exception path.
- Track actual provider usage and cost per event/load/provider/model. The
  existing reservation uses a configured reserved cost per call; production
  accounting should reconcile reservations with provider-reported usage and
  prices, and stop or route to review when the real budget is reached.

At current defaults, a reserved $0.05 per call × 20 calls gives a nominal $1
per-load ceiling. Token estimates and a reserved flat per-call amount are
controls, not proof of actual charges being at or below $1. Provider-specific
pricing, retries, long output, and accounting reconciliation need verification
before promising a cost guarantee. If the budget is exhausted, the designed
behavior is an explicit no-action plus review, not an unbudgeted fallback
model call.

## 7. Current implementation boundary and next production steps

Implemented foundations include typed domain records, PostgreSQL schema and
repositories, event ingestion, per-load decision context, SOP version/hash
validation, request/action/decision persistence, guarded stage changes,
provider and action adapter interfaces, deterministic validation, bounded
LLM usage reservations, and mock-driven tests.

Before describing this as a live production agent, complete and validate at
least:

- a production worker service that continuously claims and processes queued
  events with controlled concurrency;
- a configured provider implementation and secure secret deployment;
- production SMS/email/task/timer adapters, including delivery status,
  idempotency, and durable retries;
- a real attachment intake and text/OCR pipeline, validation, and review path;
- SOP authoring, approval, indexing, effective-date publication, and rollback
  controls;
- monitoring, alerting, retention, data isolation, access control, and
  operational runbooks;
- live-provider and database integration tests plus representative load tests,
  latency measurements, and spend reconciliation.

For local validation, see [`quickstart.md`](../specs/001-load-sop-agent/quickstart.md).
It explains the API and test setup and explicitly notes that API ingestion
enqueues events but does not itself run the worker or enable a live LLM
provider.
