# ACENT Overlay

This repository is a **fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)** with a small ACENT-specific overlay.

> **Operating principle**: do not modify upstream files. All ACENT customization
> lives under namespaced paths so we can pull upstream upgrades without merge
> conflicts.

## What's added by the overlay

```
skills/acent/                           # ACENT-namespaced skill packs
└── acent-ax-analysis/SKILL.md          # AX engine ticket analysis orchestrator
                                        # (calls into kanban-orchestrator + workers)

acent/                                  # Top-level ACENT namespace
├── README.md                           # this file
├── UPSTREAM_SYNC.md                    # how to pull from upstream
├── bridge/                             # FastAPI ↔ Hermes ACP bridge (Phase 1, AXE-20)
│   └── README.md                       # placeholder until bridge ships
└── deployment/                         # ACENT-specific deploy artifacts
    ├── README.md
    ├── Dockerfile.acent                # supplements upstream Dockerfile
    └── fly.toml                        # acent-ax-hermes Fly.io app config
```

Anything **not** under `skills/acent/` or `acent/` belongs to upstream. Edits
to upstream files require strong justification (and a plan to upstream them).

## Architecture role

This fork is the **AX engine** of the [`acent-flow`](https://github.com/acent-labs/acent-flow) ticket-analysis platform.

```
[Freshdesk FDK modal]
     │ click "분석"
     ▼
[acent-flow FastAPI]                 (acent-labs/acent-flow)
  POST /api/ax/analyze (SSE)
     │
     │ ACP / stdio
     ▼
[Hermes Agent]                       (this repo, fork of NousResearch)
  /skill acent-ax-analysis
     ├─ kanban orchestrator
     │   ├─ task: ax-analyzer
     │   ├─ task: ax-drafter
     │   └─ task: ax-reviewer
     │ (multi-agent pipeline)
     ▼
  AXAnalysisResult → SSE → modal
```

## Why a fork (not a plugin)

Hermes Agent's extension surface is **skills** (markdown) and **plugins** (Python).
A skill alone can describe the orchestration — but ACENT needs:

1. A pinned, deployable distribution of Hermes for the AX engine slot
2. The shared wire contract with `acent-flow` versioned in lock-step
3. Deployment artifacts (Dockerfile/fly.toml) for our infra
4. The ability to ship ACENT-specific config defaults

A fork-with-overlay gives all of this while keeping upstream upgradeable.

## Sister repository

- [`acent-labs/acent-flow`](https://github.com/acent-labs/acent-flow) — FastAPI orchestrator + FDK app. Ships `app/services/ax_engine_hermes/contract.py`, the wire contract this engine consumes.

## Linear

- [AX Engine Hermes 전환](https://linear.app/acent/project/ax-engine-hermes-전환-72dd94adde22)

## License

The upstream code remains under the [Hermes Agent MIT license](../LICENSE).
ACENT overlay files (`skills/acent/`, `acent/`) are also MIT, ACENT Labs.
