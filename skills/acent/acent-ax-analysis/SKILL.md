---
name: acent-ax-analysis
description: "ACENT AX Engine: orchestrate ticket analysis (analyzer → drafter → reviewer) for inbound Freshdesk tickets routed by acent-flow."
version: 0.1.0
author: ACENT Labs
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [acent, ax-engine, freshdesk, ticket-analysis, multi-agent]
    related_skills: [kanban-orchestrator, kanban-worker]
---

# ACENT AX Engine — Ticket Analysis Orchestrator

> This skill is invoked by the `acent-flow` FastAPI when a support agent
> clicks "분석" in the Freshdesk FDK modal, or by the scheduled prefetch
> cron. Hermes acts as the AX engine; the wire contract is fixed by
> `acent-flow:app/services/ax_engine_hermes/contract.py`.

## When to use

You receive a Freshdesk ticket payload conforming to `AXAnalysisRequest`.
Decompose the analysis into three specialist tasks and stream stage
events back to the caller.

| Trigger source (request field) | Behavior |
|---|---|
| `modal` (default) | Stream stage events as they happen — the caller is a human agent waiting in the modal |
| `scheduled` | Same pipeline; the caller is a cron, no rush |
| `admin_manual` | Same pipeline; caller is operations |

## Input contract

The caller passes (verbatim) a JSON object matching `AXAnalysisRequest`:

```jsonc
{
  "tenant_id": "...",
  "external_ticket_id": "...",
  "platform": "freshdesk",
  "trigger_source": "modal",
  "delivery_mode": "stream_only",
  "ticket_data": {
    "subject": "...",
    "description_text": "...",
    "tags": [...],
    "requester": {...},
    "conversation": [...]
  },
  "settings": { "model_name": "...", "language": "ko" }
}
```

You MUST treat `ticket_data` as the only source of truth for the ticket.
Do not call Freshdesk APIs directly — `acent-flow` already redacted /
prepared the payload, and the engine has no Freshdesk credentials.

## The three specialists (kanban tasks)

Create three kanban tasks in this exact order. Use the kanban
orchestrator playbook (see `kanban-orchestrator` skill).

### 1. `ax-analyzer` — intent / sentiment / urgency

Hands off to a profile that reads `ticket_data` and emits an
`AnalysisSummary` JSON:

```jsonc
{
  "intent": "billing_inquiry | technical_issue | refund_request | ...",
  "sentiment": "positive | neutral | frustrated | angry",
  "urgency_score": 0.0-1.0,
  "category": "billing | technical | account | ...",
  "escalation_risk": true|false,
  "language": "ko|en|...",
  "key_entities": [...],
  "missing_info": [...]
}
```

Stream a `stage='analyzing'` SSE event when this task starts and a
final one with the result when it completes.

### 2. `ax-drafter` — private note draft

Consumes the analyzer's output + the ticket. Emits a `DraftOutput`:

```jsonc
{
  "private_note_html": "<p>...</p>",
  "private_note_text": "...",
  "suggested_tags": [...],
  "suggested_status": "pending | resolved | ...",
  "recommended_action": "auto_resolve | suggest_reply | escalate | review | needs_info",
  "confidence": 0.0-1.0
}
```

The note must be in the ticket's language (`analysis.language`) and
written for a support agent reader, not the customer.

Stream a `stage='drafting'` SSE event with progressive HTML chunks if
the model supports streaming.

### 3. `ax-reviewer` — quality gate

Reads the drafter output and emits a `ReviewOutput`:

```jsonc
{ "verdict": "approve | revise | reject", "reason": "...", "issues": [...] }
```

If `revise`, re-route to the drafter (max attempts in
`request.settings.max_attempts`, default 3). If `reject`, emit a
terminal `stage='error'` event with `error_code='REVIEW_REJECTED'`.

Stream a `stage='reviewing'` SSE event when the reviewer task starts.

## Output contract

The terminal event is `stage='completed'` carrying an `AXAnalysisResult`:

```jsonc
{
  "run_id": "...",
  "tenant_id": "...",
  "external_ticket_id": "...",
  "analysis_summary": {...},
  "draft_output": {...},
  "review_output": {...},
  "final_private_note_html": "...",
  "final_private_note_text": "...",
  "suggested_tags": [...],
  "confidence_score": 0.0-1.0,
  "recommendation": "...",
  "usage": { "tokens_input": N, "tokens_output": N, "model_name": "..." },
  "latency_ms": N
}
```

On failure: `stage='error'` with `AXErrorInfo`
(`stage`, `error_code`, `message`, `should_retry`).

## Streaming event sequence (modal-first)

```
accepted → analyzing → drafting → reviewing → completed
                                              └─ or error
```

Emit SSE events at each stage transition, plus periodic progress events
for stages that take >2s. Each event MUST validate against `AXStreamEvent`.

## Hard constraints

- **Tenant isolation**: never mix data across `tenant_id` boundaries.
  Memory writes (if used) must be tagged with the tenant id.
- **No Freshdesk calls**: the engine has no Freshdesk credentials. All
  external writes (private notes, tag updates) happen on `acent-flow`'s
  side after this analysis finishes.
- **Schema strict**: every event/result must validate against the
  contract. Unknown fields are rejected at `acent-flow`'s boundary.
- **PII handling**: emails / phone numbers / addresses appear in
  `ticket_data`. Do not echo them into the analysis summary or drafter
  output unless required for the agent's reply context.

## Failure modes the orchestrator must handle

| Failure | Action |
|---|---|
| Analyzer can't classify intent | Emit `error_code='ANALYZER_FAILED'`, retry up to `max_attempts`. After max, emit terminal `error` |
| Drafter exceeds token budget | Truncate context (drop oldest conversation messages first), retry |
| Reviewer rejects all drafts | Terminal `error_code='REVIEW_REJECTED'`, do not commit |
| Stream consumer disconnects | Continue analysis (audit row writeback completes anyway) — the caller will reconnect or retry |

## References

- Wire contract: `acent-flow:app/services/ax_engine_hermes/contract.py`
- Kanban discipline: `kanban-orchestrator`, `kanban-worker`
- Audit DB: `ax_engine_hermes.analysis_jobs` / `analysis_results`
