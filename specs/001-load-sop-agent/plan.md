# Implementation Plan: Freight Load SOP Monitoring Agent

**Branch**: `001-load-sop-agent` | **Date**: 2026-10-17 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-load-sop-agent/spec.md`

## Summary

Build an event-driven operations agent for freight loads that processes inbound messages, pings, timers, and attachments, selects the active SOP for each load, and chooses the next valid action while recording no-action decisions explicitly. Use the configured LLM to interpret plain-language SOPs and relevant event context, returning a constrained, evidence-cited proposal. Unclear or non-applicable events always create a no-action decision; any abstention/review-needed signal is recorded separately as review metadata and cannot authorize an external action. Deterministic orchestration and validation remain authoritative for SOP precedence, allowed actions, request state, contacts/channels, and execution.

## Technical Context

**Language/Version**: Python 3.11

**Primary Dependencies**: asyncio, pydantic, httpx, python-dateutil, pytest, structlog or equivalent logging, one configured LLM provider client behind an adapter

**Storage**: Durable load ledger and event log in a relational store such as Postgres; optional blob storage for PDFs/images; S3-compatible storage if needed for attachments

**Testing**: pytest, contract tests, integration tests with mock adapters, scenario-based eval harness

**Target Platform**: Linux worker services and background jobs, deployable in a containerized environment

**Project Type**: web-service / event-driven background service

**Performance Goals**: each agent run in 5 minutes or less; 100,000 loads per month with consistent event handling and bounded LLM spend

**Constraints**: must support many client SOPs, strict no-action decisions, auditable action histories, and client override precedence; model proposals must conform to a schema, cite the active SOP version and supporting passage, and pass deterministic safety validation before execution; API credentials must be provided through secret configuration and never persisted with decision records; send only minimum required load context and verify provider data-handling terms before production

**Scale/Scope**: 50-100 inbound events per load; loads active for 2 days to 2 weeks; high growth in load volume and SOP variation; invoke the LLM only for decision-relevant events and enforce per-load call, retry, and spend limits

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Passes:
- SOP-First Decisioning: the design starts from the active SOP and client overrides before action selection.
- Evidence-Backed Memory: the plan includes an append-only load timeline, open requests, and action ledger.
- Human-in-the-Loop Escalation: escalation to Hero or broker flows are explicit in the design.
- Traceable Actioning: every outbound action ties to an event and SOP rule.
- Reliability, Cost, and Scale Discipline: deterministic event orchestration, selective LLM calls, bounded retries/spend, and fail-closed validation keep the system predictable and cost-aware.
- Operational Safety Constraints: unclear/non-applicable events produce explicit no-action decisions, review routing remains separate, and auditability and channel validation are built into the design.
- Development Workflow: validation scenarios and edge-case coverage are included.

Result: PASS

## Project Structure

### Documentation (this feature)

```text
specs/001-load-sop-agent/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── internal-integration-contracts.md
├── spec.md              # Feature specification
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
src/
├── app/
│   ├── agent/
│   │   ├── load_runner.py
│   │   ├── event_router.py
│   │   └── decision_engine.py
│   ├── memory/
│   │   ├── load_timeline.py
│   │   ├── request_store.py
│   │   └── action_ledger.py
│   ├── rules/
│   │   ├── sop_loader.py
│   │   ├── rule_selector.py
│   │   └── decision_validator.py
│   └── services/
│       ├── llm_sop_interpreter.py
│       ├── document_classifier.py
│       ├── document_extractor.py
│       └── action_executor.py
├── adapters/
│   ├── llm_provider_adapter.py
│   ├── sms_adapter.py
│   ├── email_adapter.py
│   ├── slack_adapter.py
│   ├── tms_adapter.py
│   └── timer_adapter.py
├── models/
│   ├── load.py
│   ├── event.py
│   ├── action.py
│   ├── contact.py
│   └── sop.py
├── workers/
│   ├── ingestion_worker.py
│   └── load_worker.py
└── config/
    └── settings.py

tests/
├── contract/
│   ├── test_integration_contracts.py
│   └── test_sop_rules.py
├── integration/
│   ├── test_delay_risk_flow.py
│   ├── test_no_action_flow.py
│   └── test_client_override_flow.py
└── unit/
    ├── test_rule_priority.py
    ├── test_open_request_resolution.py
    └── test_document_classification.py
```

**Structure Decision**: A single Python service with an event-driven worker model and isolated adapters is the correct fit for this project because the system is operationally focused, internally orchestrated, and designed to be mockable under high event volume. The single configured LLM provider is isolated behind an adapter; the model proposes an outcome from the active SOP, while the existing deterministic orchestration and validator enforce safety and execute only approved catalog actions. This uses the available API key without tying policy logic to provider-specific code or treating model output as executable instructions.

## Complexity Tracking

No constitution violations require justification. The LLM is used for language interpretation, not as an unrestricted executor; deterministic orchestration, explicit fallbacks, bounded calls, and auditable evidence preserve the constitution's reliability and cost discipline.
