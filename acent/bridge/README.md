# acent-ax-bridge

FastAPI ↔ Hermes ACP bridge for the ACENT AX engine.

> Linear: [AXE-20](https://linear.app/acent/issue/AXE-20)

## Role

Translates `acent-flow`'s HTTP/SSE surface (`POST /v1/analyze`) into
Hermes' stdio JSON-RPC ACP protocol, routing to the
`acent-ax-analysis` skill in this fork's overlay.

```
acent-flow FastAPI            (HTTP)
        │  POST /v1/analyze
        ▼
acent-ax-bridge (this pkg)    (FastAPI)
        │  spawn_agent_process(hermes acp ...)
        ▼
hermes-agent acp_adapter      (stdio JSON-RPC)
        │  /skill acent-ax-analysis
        ▼
ax-analyzer / ax-drafter / ax-reviewer (kanban workers)
```

## Wire contract

`ax_bridge/contract.py` is **vendored** from
[`acent-flow:app/services/ax_engine_hermes/contract.py`](https://github.com/acent-labs/acent-flow/blob/main/app/services/ax_engine_hermes/contract.py).
Bump `CONTRACT_VERSION` and update both copies in lock-step.

## Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/v1/analyze` | SSE stream of `AXStreamEvent`. Bearer-token authed. |
| `GET`  | `/healthz`    | Liveness. |

## Run locally

```bash
cd acent/bridge
python3.13 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"

# Set the shared bearer token
export AX_ENGINE_INTERNAL_TOKEN=dev-token

# Run
uvicorn ax_bridge.main:app --port 8001 --reload
```

In another terminal:
```bash
curl -N -X POST http://localhost:8001/v1/analyze \
  -H "Authorization: Bearer dev-token" \
  -H "Content-Type: application/json" \
  -d '{
    "tenant_id": "t1",
    "external_ticket_id": "42",
    "ticket_data": {"subject": "billing", "description_text": "missing invoice"}
  }'
```

The bridge will spawn `hermes acp` (per request), drive the ACP
handshake, and stream session updates back as
`text/event-stream` frames carrying `AXStreamEvent` JSON.

## Stage inference

ACP delivers fine-grained chunks (text, thoughts, tool calls). The
bridge collapses them into the modal-first stages defined in the
contract (`accepted`, `analyzing`, `drafting`, `reviewing`,
`completed`, `error`).

The current heuristic infers stage from kanban-task profile names
(`ax-analyzer`, `ax-drafter`, `ax-reviewer`). Tightening will follow
once we observe real Hermes transcripts running the
`acent-ax-analysis` skill — see `ax_bridge/translate.py` for the
mapping.

## Per-request spawn

Phase 1 spawns one Hermes ACP subprocess per inbound request. Simple,
slow (cold-start latency on first prompt), tenant-isolated by
construction. Pool / shared-process model is a Phase 4 follow-up.

## Tests

```bash
pytest                  # 6 translator unit tests, no Hermes runtime needed
```

End-to-end smoke (requires `hermes` on PATH and an LLM API key):

```bash
# Make sure hermes is configured (model + provider key)
hermes doctor

# Then drive the bridge with a real ticket payload
uvicorn ax_bridge.main:app --port 8001 &
curl -N -X POST http://localhost:8001/v1/analyze \
  -H "Authorization: Bearer dev-token" \
  -d @tests/fixtures/sample_request.json
```

(End-to-end fixture lands in a follow-up PR with the real Hermes
transcripts to tighten `translate.py`.)

## Open items

- The orchestrator skill emits the `COMPLETED` payload as the final
  ACP message. The current driver synthesizes a terminal event on
  prompt completion; a follow-up will parse the structured result
  payload from Hermes' last `AgentMessageChunk`.
- Tool-call permission policy (`hermes_client.py`) is overly strict
  by default — auto-allow is restricted to `think`/`search`/`read`.
  Will need tuning when the `ax-analyzer` skill needs additional
  tools (e.g. RAG against the tenant's KB).
- Multi-tenant isolation through Hermes memory: per-request spawn
  guarantees no leak across requests, but if we add a process pool,
  tenant-tagged memory namespaces become required.
