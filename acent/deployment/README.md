# ACENT — Deployment

ACENT-specific deployment artifacts. **Supplements upstream's
`Dockerfile` and packaging — never overrides them.**

## Files

- `Dockerfile.acent` — extends the upstream Dockerfile with ACENT skill
  packs and the Phase 1 ACP bridge (when AXE-20 lands)
- `fly.toml` — Fly.io app config for `acent-ax-engine` (Narita region)

## Why a separate Dockerfile

Upstream `Dockerfile` builds the standalone Hermes Agent. Our
deployment additionally needs:

- ACENT environment defaults (model, Linear key, etc.)
- The ACP bridge process exposed on a public port
- Healthcheck wired to the bridge, not Hermes itself

We extend rather than replace so that an upstream Dockerfile bump does
not silently break our build:

```dockerfile
ARG UPSTREAM_BASE=ghcr.io/nousresearch/hermes-agent:latest
FROM ${UPSTREAM_BASE}
# add ACENT layer ...
```

(Concrete contents land with AXE-5 follow-up + AXE-20.)

## Fly.io

Single app slot reserved: `acent-ax-engine` (Narita). Reuses what was
previously the `acent-ax-paperclip` slot (paperclip Fly app destroyed
during Phase 2 cutover).
