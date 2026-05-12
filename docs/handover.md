# acent-ax-engine — Handover

> **Single source of truth** for the ACENT-specific overlay on top of NousResearch/hermes-agent.
> 새 작업자(사람/Claude/Codex)는 이 문서 + `CLAUDE.md` + `AGENTS.md` (upstream)만
> 읽으면 충분합니다.
>
> **Last meaningful update**: 2026-05-11

---

## 0. What this repo is

**Fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)** + small ACENT-specific overlay.

**역할**: ACENT Flow ticket-analysis 플랫폼의 **AX 엔진**.

```
[Freshdesk FDK modal]
     │ click "분석"
     ▼
[acent-flow FastAPI]                 (acent-labs/acent-flow)
  POST /api/ax/analyze (SSE)
     │
     │ ACP / stdio
     ▼
[Hermes Agent]                       (이 repo, fork)
  /skill acent-ax-analysis
     ├─ kanban orchestrator
     │   ├─ task: ax-analyzer
     │   ├─ task: ax-drafter
     │   └─ task: ax-reviewer
     │ (multi-agent pipeline)
     ▼
  AXAnalysisResult → SSE → modal
```

전체 아키텍처/계약/Phase 흐름은 sister repo 문서 참조: `acent-flow/docs/handover.md`.

---

## 1. Operating principle (절대 룰)

**Upstream 파일은 수정하지 않는다.** 모든 ACENT 커스터마이즈는 namespaced path 안에:

```
skills/acent/                       # ACENT 스킬 팩
└── acent-ax-analysis/SKILL.md      # AX 엔진 ticket 분석 orchestrator

acent/                              # Top-level ACENT namespace
├── bridge/                         # FastAPI ↔ Hermes ACP bridge (Phase 1, AXE-20)
├── deployment/                     # ACENT-specific deploy artifacts
└── profiles/                       # 워커 프로파일 (예정, AXE-30)
    ├── ax-analyzer/
    ├── ax-drafter/
    └── ax-reviewer/

CLAUDE.md                           # Claude 자동 로드 — 협업 + heartbeat 자동등록
docs/handover.md                    # 이 문서
.gitignore                          # 우리가 1줄 추가 (.claude/, .context/) — 예외 허용
```

**예외 허용** (사용자 승인 후):
- `.gitignore` 같은 tooling 파일에 1-2줄 추가 (overlay state 무시)
- root `CLAUDE.md` 작성 (upstream에 없으므로 충돌 X — 우리 신규 파일)

**금지**:
- `agent/`, `gateway/`, `hermes_cli/`, `skills/<not-acent>/`, root `Dockerfile`, root `pyproject.toml` 등 upstream 파일 직접 수정
- ACENT 파일을 upstream path로 rename
- `main` force-push

---

## 2. 협업 프로토콜 (3-actor)

| 역할 | 누가 | 책임 |
|---|---|---|
| **PO** | 대표님 | 방향 결정, contract 협의, Go-live 승인 |
| **PM / Architect / Integration Gate** | Claude | 명세 정리, Linear 분해, evidence 검수, 머지 결정 |
| **Implementer + Evidence Producer** | Codex | bounded PR 구현, technical veto, 스크린샷/로그/테스트 산출 |

### 핵심 3규칙

1. **Linear 이슈 = 결정 보관소** — commit message나 채팅에 결정 남기지 말 것.
2. **Codex feasibility review의 거부권 존중** — "이 명세 안 됨" 하면 명세 다시 조정.
3. **Evidence 없는 머지 금지** — 스크린샷/로그/테스트 결과 없으면 Claude 거절.

자세한 사이클 흐름과 Linear 분담은 `acent-flow/docs/handover.md` §1 참조 (동일 내용).

---

## 3. ACP Bridge (Phase 1, AXE-20)

`acent/bridge/` 패키지 — `acent-flow` FastAPI ↔ Hermes Agent (ACP).

### Surface

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /healthz` | none | Liveness 프로브 — `{status: ok, ...}` |
| `POST /v1/analyze` | `Authorization: Bearer $AX_ENGINE_INTERNAL_TOKEN` | SSE 분석 스트림 |

`POST /v1/analyze`는 `AXAnalysisRequest` JSON body 받아 `AXStreamEvent` SSE frame 출력 (`event:` 필드 = `stage` 값: `accepted` → … → `completed`/`error`).

### Architecture

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
SubprocessHermesTransport — 요청당 1 `hermes acp` process spawn
```

### 테스트

```bash
cd acent/bridge
python3.13 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
```

현재 상태: `codex/axe-20-hermes-sse-bridge` 브랜치, **31/31 tests pass**, evidence (E2E) 부족으로 머지 보류.

### Wire contract

`acent-flow:app/services/ax_engine_hermes/contract.py`와 동일 (AXE-22). `ax_bridge/contract.py`는 verbatim vendored copy — **여기서 재정의하지 말 것**, contract version 변경 시 양쪽 lock-step.

### Phase 1 결정

- **Per-request spawn**: 분석 1회당 `hermes acp` subprocess 1개. Pool / persistent 재사용은 Phase 4 (AXE-1 EPIC).
- **Tenant isolation**: process boundary가 Phase 1의 isolation surface. spawned Hermes는 clean env, 공유 session memory 없음.
- **Backpressure**: bridge에서 처리 X. gateway-side rate limit는 acent-flow.
- **Runtime smoke**: full `hermes acp` 통합은 `acent/deployment/` 컨테이너에서 검증.

### Out of scope (Phase 1)
- Process pool / backpressure
- Persistent agent sessions / multi-tenant isolation
- Freshdesk write-back
- Upstream Hermes source modification

---

## 4. Worker Profiles (예정, AXE-30)

`skills/acent/acent-ax-analysis/SKILL.md`는 kanban-orchestrator로 dispatch할 3개 워커 프로파일을 가정하지만 **실제 정의 부재**. AXE-30에서 작성.

```
acent/profiles/
├── ax-analyzer/
│   ├── SOUL.md             # role/persona/금지사항 (Korean ticket 처리)
│   ├── config.yaml         # gemini-2.5-pro, structured output, max_tokens
│   └── output-schema.json  # AnalysisSummary (intent/sentiment/urgency/...)
├── ax-drafter/
│   ├── SOUL.md
│   ├── config.yaml
│   └── output-schema.json  # DraftOutput (private_note_html/text/...)
└── ax-reviewer/
    ├── SOUL.md
    ├── config.yaml
    └── output-schema.json  # ReviewOutput (verdict/reason/issues)
```

`acent/profiles/eval.py` — sample tickets → 워커 호출 → contract 검증 harness.

워커 프로파일이 정의돼야 kanban orchestrator가 dispatch 가능. 그 전엔 파이프라인 전체가 실행 불가.

---

## 5. Deployment

### Files

```
acent/deployment/
├── Dockerfile.acent        # upstream Dockerfile 위에 ACENT 레이어
└── fly.toml                # acent-ax-engine Fly.io app config
```

### 왜 별도 Dockerfile

upstream `Dockerfile`은 standalone Hermes Agent 빌드. 우리는 추가로:
- ACENT environment defaults (모델, Linear key 등)
- ACP bridge process를 public port에 노출
- Healthcheck를 bridge로 wiring

```dockerfile
ARG UPSTREAM_BASE=ghcr.io/nousresearch/hermes-agent:latest
FROM ${UPSTREAM_BASE}
# add ACENT layer ...
```

### Fly.io

- App: `acent-ax-engine` (Narita region)
- 단일 process group `app`: `hermes dashboard` 띄우고 그 안에서 `hermes gateway run`을 자식 spawn (로컬 사용 패턴 일치)
- Public ingress: dashboard 9119 → 외부 노출 (URL semi-secret + GEMINI_API_KEY 정기 회전으로 완화)
- Gateway service: 8642 → acent-flow가 호출
- Health check: `/api/status` 200 + gateway_running

상세 fly.toml은 `acent/deployment/fly.toml` 참조.

### 현재 상태
- ✅ 배포 동작 (`https://acent-ax-engine.fly.dev/api/status` 200)
- ⚠️ Dashboard 인증 부재 — `/api/env/reveal`로 GEMINI_API_KEY 노출 가능. Phase 4에 Caddy Basic Auth sidecar 예정.

---

## 6. Upstream Sync

### Remotes

```
origin    https://github.com/acent-labs/acent-ax-engine.git
upstream  https://github.com/NousResearch/hermes-agent.git
```

`upstream` 누락 시:

```bash
git remote add upstream https://github.com/NousResearch/hermes-agent.git
```

### Routine sync (recommended weekly)

```bash
git fetch upstream
git checkout main
git pull --ff-only origin main
git merge upstream/main --no-ff -m "chore: merge upstream/main"

# 검증
hermes --version
ls skills/acent/
ls acent/

git push origin main
```

`git merge` conflict 시 → upstream이 우리 overlay path와 collide하는 file 추가했을 가능성. **resolution 전에 조사**. 우리 path를 rename할지언정 perpetual conflict는 carry 하지 않음.

### Major version upgrade (e.g. v0.13 → v0.14)

추가로:
1. upstream `RELEASE_v*.md` 읽고 breaking changes 확인
2. `acent-ax-analysis` 스킬 재테스트 (`hermes /skill acent-ax-analysis` 또는 acent-flow ACP 통해)
3. acent-flow `tests/test_ax_analyze_route.py` 이 엔진에 대해 실행
4. upstream Dockerfile 변경 시 `acent/deployment/Dockerfile.acent` 업데이트

### Hot-fix while sync pending

```bash
git fetch upstream
git cherry-pick <upstream-sha>
```

isolated upstream commit은 cherry-pick OK. full merge는 정기 sync window용.

### What NOT to do

- upstream 파일 직접 수정 ❌ (대신 upstream에 contribute 또는 `acent/patches/` 명시)
- ACENT 파일을 upstream path로 rename ❌
- `main` force-push ❌

---

## 7. 환경변수

```env
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
GEMINI_API_KEY=...                # fly secret으로 주입, 정기 회전 권장
GEMINI_MODEL=gemini-2.5-pro

AX_ENGINE_INTERNAL_TOKEN=...      # FastAPI ↔ AX Engine 인증
SSE_KEEPALIVE_INTERVAL_S=15
API_SERVER_KEY=...                # OpenAI 호환 API key gate (gateway)

# Phase 3 옵션 모드
QUEUE_POLL_INTERVAL_S=30
QUEUE_BATCH_SIZE=5
```

---

## 8. Linear

- [AX Engine Hermes 전환](https://linear.app/acent/project/ax-engine-hermes-전환-72dd94adde22)
- 팀 `AXE`
- Phase 1 핵심 이슈:
  - AXE-20 (이 repo) — Hermes ACP 브리지: `codex/axe-20-hermes-sse-bridge` 작성됨, evidence 보강 후 머지
  - AXE-30 (예정) — 워커 프로파일 정의
  - AXE-31 (sister repo) — Attachment contract 추가

---

## 9. Sister 자료

- **[`acent-labs/acent-flow`](https://github.com/acent-labs/acent-flow)** — FastAPI orchestrator + FDK 모달. 전체 contract/architecture는 `acent-flow/docs/handover.md`
- License: upstream + ACENT overlay 모두 MIT
