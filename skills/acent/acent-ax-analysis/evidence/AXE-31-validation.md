# AXE-31 — `acent-ax-analysis` Hermes runtime validation

Status: Validated against `acent-flow:app/services/ax_engine_hermes/contract.py` (CONTRACT_VERSION `v1`).
Date: 2026-05-11
Runner: Hermes Agent v0.13.0 (`/Users/alan/.local/bin/hermes`)
Model: `openai/gpt-5.5` (Nous Portal)
Branch: `codex/axe-skill-runtime-validation` (forked from latest `main`)

## How to reproduce

The skill lives in this fork only. The system-wide Hermes CLI scans
`~/.hermes/skills/<category>/<name>/SKILL.md`, so we register the fork by
symlink — no copy, no upstream-file edits.

```bash
# 1. Make the skill discoverable to the local Hermes runtime
mkdir -p ~/.hermes/skills/acent
ln -s /Users/alan/GitHub/acent-ax-engine/skills/acent/acent-ax-analysis \
      ~/.hermes/skills/acent/acent-ax-analysis

hermes skills list | grep acent-ax-analysis
# → │ acent-ax-analysis │ acent │ local │ local │ enabled │

# 2. Run a one-shot validation with the sample payload
hermes chat \
  -s acent-ax-analysis \
  --max-turns 8 -Q --yolo \
  -q "$(cat /tmp/axe31_prompt.txt)" \
  > evidence/run.log
```

Prompt body (`/tmp/axe31_prompt.txt`) and sample request payload
(`sample_ticket_request.json`) are stored alongside this document.

## Independent contract validation

Outputs were not trusted from the model's self-report. They are validated
in a separate Python process against the real Pydantic models:

```bash
cd /Users/alan/GitHub/acent-flow
uv run python -c "
import sys; sys.path.insert(0, 'app')
from app.services.ax_engine_hermes.contract import AXStreamEvent, AXAnalysisResult
EV = '/Users/alan/GitHub/acent-ax-engine/skills/acent/acent-ax-analysis/evidence'
for i, line in enumerate(open(f'{EV}/sse_events.ndjson'), 1):
    line = line.strip()
    if line:
        AXStreamEvent.model_validate_json(line); print(f'SSE#{i} OK')
AXAnalysisResult.model_validate_json(open(f'{EV}/final_result.json').read())
print('Result OK')
"
```

Output:

```
[SSE#1] OK stage=accepted progress=0.0
[SSE#2] OK stage=analyzing progress=0.33
[SSE#3] OK stage=drafting progress=0.66
[SSE#4] OK stage=reviewing progress=0.9
[SSE#5] OK stage=completed progress=1.0
[Result] OK status=completed rec=escalate conf=0.88

All artifacts validate against contract.py (AXStreamEvent + AXAnalysisResult).
```

## Per-stage observation

| Stage | Hermes emitted | Contract `StreamStage` | Notes |
|---|---|---|---|
| `accepted`  | ✓ progress 0.00 | OK | Echoes correlation_id / trigger_source inside `data` |
| `analyzing` | ✓ progress 0.33 | OK | `data.analysis_summary` carries `ax-analyzer` simulated output |
| `drafting`  | ✓ progress 0.66 | OK | `data.draft_output` carries Korean private note (HTML + text) |
| `reviewing` | ✓ progress 0.90 | OK | `data.review_output.verdict = "approve"` |
| `completed` | ✓ progress 1.00 | OK | Terminal event; `AXAnalysisResult` validated separately |
| `fetching`  | n/a            | OK to skip | Modal-first uses inline `ticket_data`; not exercised in this run |
| `error`     | n/a            | n/a | Happy path; failure flow not yet exercised — see follow-up |

## SKILL.md ↔ contract.py mismatches found and fixed

Before this run, the SKILL.md output-contract example described fields that
would not actually round-trip through `AXAnalysisResult` (which uses
`ConfigDict(extra="forbid")`). Five concrete issues identified, patched in
the same branch:

| # | Mismatch in SKILL.md | Contract truth | Fix |
|---|---|---|---|
| 1 | `"run_id": "..."` in result example | Field is `job_id: UUID`. `run_id` would be rejected | Replaced example with `job_id` |
| 2 | `"external_ticket_id": "..."` in result example | Not in `AXAnalysisResult`. Extra field → rejected | Removed; added explicit note that ticket id is request-only |
| 3 | `"usage": { tokens_input, tokens_output, model_name }` nested | Fields are FLAT on the result | Flattened to `tokens_input` / `tokens_output` / `model_name` / `latency_ms` |
| 4 | `status` / `completed_at` / `tenant_id` not shown | All three are required by `AXAnalysisResult` (`status` non-optional, `completed_at` non-optional, `tenant_id` non-optional) and `validate_status_outputs` requires `analysis_summary` when `status="completed"` | Added all four to the example + validator note |
| 5 | Streaming sequence omits `fetching` and doesn't flag `extra="forbid"` on `AXStreamEvent` | `StreamStage.FETCHING` exists; both event/result models forbid extra top-level fields | Documented modal-first skip of `fetching`; noted custom fields must live under `data` |

These edits are confined to `skills/acent/acent-ax-analysis/SKILL.md` per
AXE-31 scope. `acent/bridge/` (AXE-20) and upstream Hermes files were not
touched.

## Out-of-scope items / follow-ups for AXE-20

* Failure-path SSE shape (`stage="error"` + `AXErrorInfo`) was described
  textually but not exercised at runtime. AXE-20 bridge should add an
  integration test that forces an analyzer failure and asserts the
  emitted event validates as `AXStreamEvent` and the `data` payload as
  `AXAnalysisError`.
* The Hermes CLI run produces *artifacts shaped like* the wire contract;
  in production AXE-20 must wire the actual ACP/stdio bridge so events
  arrive without the LLM-as-narrator step used here.
* The `kanban-orchestrator` task-spawn path was simulated (`validation-dry-run`)
  rather than executed end-to-end. Worth a separate integration run once
  kanban infrastructure is available in CI.

## Files in this evidence pack

```
evidence/
├── AXE-31-validation.md          # this document
├── sample_ticket_request.json    # AXAnalysisRequest payload used as input
├── sse_events.ndjson             # AXStreamEvent stream (one event per line)
└── final_result.json             # AXAnalysisResult emitted by the skill
```
