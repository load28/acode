# acode 작업 규칙 (모든 세션 공통)

이 저장소의 모든 작업은 **태스크 단위로 문서화**된다. 세션이 바뀌어도
`docs/tasks/INDEX.md`만 읽으면 전체 진행 상황을 파악하고 이어서 작업할 수
있어야 한다.

## 세션 시작 시 (필수)

1. `docs/tasks/INDEX.md`를 읽고 진행 중(`in_progress`)/차단(`blocked`) 태스크를 확인한다.
2. 이어서 할 작업이면 해당 태스크 문서의 **진행 상황**과 **다음 단계**를 읽고 재개한다.
3. 새 작업이면 새 태스크 문서를 만든다 (아래 절차).

## 새 태스크 생성 절차

1. INDEX에서 마지막 태스크 번호를 확인하고 다음 번호를 딴다: `TASK-NNNN`
2. `docs/tasks/TEMPLATE.md`를 복사해 `docs/tasks/TASK-NNNN-<slug>.md` 생성
3. `docs/tasks/INDEX.md` 표에 한 줄 추가 (status: `in_progress`)
4. 그 다음에 실제 작업을 시작한다 — **작업 먼저, 문서 나중 금지**

## 작업 중 기록 (태스크 문서에)

- **파일 단위 변경 기록**: 생성/수정/삭제한 모든 파일을 "파일별 작업 기록"
  표에 남긴다 — 무엇을 왜 바꿨고 결과가 어땠는지.
- **진행 체크리스트**: 단계가 끝날 때마다 체크한다.
- **결정 사항**: 설계 선택, 우회, 트레이드오프는 이유와 함께 기록한다.
  (예: 어떤 라이브러리를 왜 배제했는지 — 다음 세션이 같은 삽질을 반복하지 않도록)
- **검증 결과**: 테스트/스모크 결과를 실제 수치로 기록한다 ("통과" 말고 "47 passed").

## 태스크 상태

| status | 의미 |
|---|---|
| `todo` | 계획만 잡힘, 착수 전 |
| `in_progress` | 진행 중 |
| `blocked` | 외부 입력/결정 대기 (문서에 차단 사유 명시) |
| `done` | 완료 + 검증됨 |

## 태스크 종료 시 (필수)

1. 태스크 문서의 검증 결과·최종 상태·후속 작업(있다면)을 채운다.
2. `docs/tasks/INDEX.md`의 status/완료일/터치한 주요 파일을 갱신한다.
3. 남은 일이 있으면 별도 `todo` 태스크로 INDEX에 등록해 둔다 (핸드오프).

## 커밋 규칙

- 커밋 메시지 첫 줄이나 본문에 태스크 ID를 포함한다: `TASK-NNNN`
- 태스크 문서/INDEX 갱신은 해당 작업과 **같은 커밋**에 포함한다
  (코드와 기록이 어긋난 채로 푸시하지 않는다).

## 개발 명령

```bash
pip install -e '.[adk,dev]'   # 설치
pytest                        # 테스트 (전부 통과 상태 유지)
acode serve                   # MCP 서버 (stdio)
```

## 저장소 구조 요약

- `src/acode/astcore` — tree-sitter 파싱, AST 핑거프린트, 결정적 룰 엔진
- `src/acode/rag` — SQLite 컨벤션 저장소, 검색, 코드베이스 인덱서
- `src/acode/llm` — LLM 프로바이더 (claude CLI 기본, API 폴백)
- `src/acode/agent` — 파이프라인 단계 + ADK 에이전트 오케스트레이션
- `src/acode/mcpserver` — FastMCP stdio 서버
- `conventions/` — 시드 컨벤션 JSON
- `docs/tasks/` — 태스크 기록 (이 문서의 대상)
