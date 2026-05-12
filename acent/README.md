# ACENT overlay

이 디렉토리는 NousResearch/hermes-agent fork의 ACENT-specific 오버레이입니다.
Upstream 파일은 수정하지 않습니다 (overlay rule).

**전체 컨텍스트**: [`../docs/handover.md`](../docs/handover.md) — overlay 구조, bridge, deployment, upstream sync, 협업 프로토콜, current state.

**자동 로드되는 AI 지침**: [`../CLAUDE.md`](../CLAUDE.md) (Claude) / [`../AGENTS.md`](../AGENTS.md) (Codex, 최상단에 ACENT pointer).

## 디렉토리 구성

- `bridge/` — FastAPI ↔ Hermes ACP bridge (Phase 1, AXE-20)
- `deployment/` — Fly.io 배포 artifacts (`Dockerfile.acent`, `fly.toml`)
- `profiles/` — kanban 워커 프로파일 (예정, AXE-30)

세부 사양은 모두 `docs/handover.md`에.
