# ACENT — FastAPI ↔ Hermes ACP Bridge (placeholder)

> **Status**: placeholder. The bridge code lands with [AXE-20](https://linear.app/acent/issue/AXE-20).

This directory will house the thin Python layer that bridges
`acent-flow` FastAPI (`POST /api/ax/analyze` SSE) to Hermes via the
Agent Client Protocol (ACP, stdio).

## Why a bridge

- Hermes ACP is **stdio-based**, but `acent-flow` needs an **HTTP/SSE**
  surface for the FDK browser.
- The bridge:
  1. Accepts the `AXAnalysisRequest` JSON from `acent-flow`
  2. Spawns (or attaches to) a Hermes ACP process with `/skill acent-ax-analysis`
  3. Translates Hermes' streaming output into `AXStreamEvent` SSE frames
  4. Closes / cleans up the Hermes session on terminal stage

## Open questions (resolved during AXE-20)

- **Per-request spawn vs persistent agent pool**: cold-start latency vs concurrency
- **Tenant isolation**: separate Hermes process per tenant? Shared process with tagged memory?
- **Backpressure**: what to do if a tenant pushes 100 modal clicks/min
- **Resource limits**: max concurrent Hermes processes, memory/CPU caps

## Wire contract

Identical to `acent-flow:app/services/ax_engine_hermes/contract.py`.
The bridge re-uses those Pydantic models — *do not redefine them here*.

## Linear

- [AXE-20](https://linear.app/acent/issue/AXE-20) — AX Engine SSE HTTP endpoint (this bridge)
- [AXE-22](https://linear.app/acent/issue/AXE-22) — shared contract (lives in `acent-flow`)
