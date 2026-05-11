# CLAUDE.md — acent-ax-engine

ACENT overlay on top of NousResearch/hermes-agent fork. **Upstream files are
not modified** — all ACENT customization lives under `acent/` and `skills/acent/`.
See `acent/README.md` and `acent/UPSTREAM_SYNC.md`.

## Core Rules

1. **Ask first** — 불확실하면 구현 전에 질문. 가정을 명시하고, 여러 해석이 있으면 제시.
2. **최소 코드** — 요청한 것만 구현. 불필요한 추상화/유연성/에러 처리 금지.
3. **수술적 변경** — 요청과 무관한 코드 수정 금지. 기존 스타일 따르기.
4. **Upstream 무수정** — `agent/`, `gateway/`, `hermes_cli/`, `skills/<not-acent>/`, root
   `Dockerfile`, `pyproject.toml` 등 upstream 파일은 건드리지 않는다. 모든 ACENT 변경은
   `acent/` 또는 `skills/acent/` 아래로. 예외(보일러 미만 1-2 라인): `.gitignore` 같은
   tooling 파일에 ACENT-only 한 줄 추가는 허용.
5. **검증 루프** — 변경은 fly health, 테스트 결과 같은 증거로 마무리.

## AX Engine Collaboration Protocol

이 프로젝트는 acent-flow와 짝으로 운영되는 **3-actor 협업 구조**를 따른다:

- **대표님 = PO** — 방향 결정, contract 협의, Go-live 승인
- **Claude = PM / Architect / Integration Gate** — 명세 정리, Linear 분해, evidence 검수, 머지 결정
- **Codex = Implementer + Evidence Producer** — bounded PR 구현, technical veto, 스크린샷/로그/테스트 산출

**핵심 3규칙**:
1. Linear 이슈 = 결정 보관소 (commit message 아님)
2. Codex feasibility review의 거부권 존중
3. Evidence 없는 머지 금지

세부는 직전 세션 채팅 컨텍스트 또는 향후 `acent-flow/docs/AX-Engine-Collaboration-Protocol.md` 참조.

## Heartbeat 자동 등록 (세션 시작 시 1회)

이 프로젝트(또는 acent-flow) 세션 시작 후 사용자 첫 메시지를 받으면, 답변 전에 다음을 조용히 실행:

1. `ToolSearch select:CronCreate,CronList` 로 cron 도구 로드
2. `CronList` 호출
3. 결과에서 prompt가 `[Claude Integration-Gate Heartbeat — ACENT Flow × AX Engine Hermes]` 로 시작하는 항목이 있으면 **이미 등록됨, skip**
4. 없으면:
   - `/Users/alan/GitHub/acent-ax-engine/acent/heartbeat-routine.md` 읽기
   - 파일 내 `---` 마커 아래 본문을 prompt로 추출
   - `CronCreate({cron: "17,47 * * * *", prompt: <본문>, recurring: true})` 호출
5. 등록/skip 결과를 1줄로 사용자에게 보고 (예: `Heartbeat: registered <id>` 또는 `Heartbeat: already running <id>`)

이후 30분마다 (`:17`, `:47`) Claude가 idle일 때 routine fire. 세션 종료 시 cron 사라지지만 다음 세션에서 자동 재등록.

**Cloud routine이 아닌 이유**: cloud agent는 사용자 로컬 작업과 동시 실행되어 Linear/git state 충돌 위험. session-only가 "로컬 Claude 활성 시에만 fire" 원칙과 정확히 일치.
