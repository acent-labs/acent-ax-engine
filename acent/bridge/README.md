# ACENT — FastAPI ↔ Hermes ACP Bridge

> **Phase 1, AXE-20.** Modal-first SSE entry point. Pool / backpressure /
> writeback queue work is deferred to later phases.

This package is the thin Python layer that bridges
`acent-flow` FastAPI (`POST /api/ax/analyze` SSE) to Hermes Agent via the
[Agent Client Protocol (ACP)](https://github.com/zed-industries/agent-client-protocol).

## Surface

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `GET /healthz` | none | Liveness probe — returns `{status: ok, ...}` |
| `POST /v1/analyze` | `Authorization: Bearer $AX_ENGINE_INTERNAL_TOKEN` | SSE analysis stream |

`POST /v1/analyze` accepts an `AXAnalysisRequest` JSON body and emits
`AXStreamEvent` SSE frames whose `event:` field is the `stage` value
(`accepted` → … → `completed` | `error`).

## Architecture

```
acent-flow FastAPI
   │  POST /v1/analyze (bearer)
   ▼
ax_bridge.server.analyze
   │
   ▼
AnalysisSession.stream()   ← yields AXStreamEvent
   │  ACCEPTED ─►
   │  ACP session/update via HermesTransport
   │   ─► StageTranslator ─► AXStreamEvent ─►
   │  COMPLETED | ERROR ─►
   ▼
SubprocessHermesTransport — spawns one `hermes acp` process per request
```

## Testing

```bash
cd acent/bridge
python3.13 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

## Wire contract

Identical to `acent-labs/acent-flow:app/services/ax_engine_hermes/contract.py`
(AXE-22). The file `ax_bridge/contract.py` is a verbatim vendored copy —
**do not redefine** these models in this package; update both copies in
lock-step when the contract version moves.

## Phase 1 decisions

- **Per-request spawn**: one `hermes acp` subprocess per analysis. Pool /
  persistent agent reuse is deferred to Phase 4 (see AXE-1 EPIC).
- **Tenant isolation**: process boundary acts as the isolation surface in
  Phase 1. A spawned Hermes inherits a clean env, no shared session memory.
- **Backpressure**: not handled at the bridge in Phase 1. The gateway-side
  rate limit lives in `acent-flow`.
- **Runtime smoke**: full `hermes acp` integration is exercised by the
  `acent/deployment/` container, not by `acent/bridge` unit tests.

## Out of scope (Phase 1)

- Process pool / backpressure
- Persistent agent sessions / multi-tenant isolation
- Freshdesk write-back
- Upstream Hermes source modification

## Linear

- [AXE-20](https://linear.app/acent/issue/AXE-20) — AX Engine SSE HTTP endpoint (this bridge)
- [AXE-22](https://linear.app/acent/issue/AXE-22) — shared contract (lives in `acent-flow`)
