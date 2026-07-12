# TASK-0005: 메타데이터 필터 와일드카드 시맨틱 수정 (실동작 데모에서 발견)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/coding-agent-ast-rag-mcp-vzqm5x` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | session_018XxtzruUVu3MpJqCTkMQN9 |

## 목표

MCP 실동작 데모(생성/수정 실제 LLM 호출) 중 발견한 검색 시맨틱 버그 수정.

**증상**: `generate_code(..., framework="fastapi")` 호출 시
`mechanical_report.checked_rules: []` — framework 메타데이터가 없는 일반
룰(py-no-print 등)이 하드 필터에서 전부 탈락해, **룰 0개로 "검증 통과"**가
나왔다. 검증이 사실상 무력화되는 심각한 시맨틱 결함.

**원인**: `_metadata_matches`가 "엔트리에 키가 없으면 불일치"로 처리.
일반(제약 없는) 룰은 모든 프레임워크에 적용돼야 하므로 반대가 맞다.

## 진행 상황

- [x] 하드 필터를 와일드카드 시맨틱으로: 엔트리가 키를 선언하지 않으면 모든 값에 매치,
      선언했으면 일치해야 통과
- [x] 1차 수정 후 데모 재실행에서 2차 버그 발견: 와일드카드가 소프트 점수
      (`_metadata_overlap`)에도 새어 들어가 키 없는 엔트리가 overlap 1.0을
      받음 → **선언된 키가 일치할 때만** 점수를 주도록 분리
- [x] 회귀 테스트 2개 추가 (와일드카드 매치 + 정확 매치 우선 랭킹, 생성 경로 룰 적용)
- [x] 실동작 재검증: checked_rules 7개, fastapi 패턴이 score 1.0으로 1위

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/rag/store.py` | 수정 | `_metadata_matches`: 엔트리에 없는 키는 와일드카드로 통과. `_metadata_overlap`: 반대로 선언된 키의 일치만 점수화 (와일드카드는 필터만 통과하고 랭킹에서는 정확 매치 아래) | 90 passed |
| `tests/test_rag.py` | 수정 | `test_generic_entry_matches_any_metadata_value` (generic 포함 + 타 프레임워크 배제 + 정확 매치 우선), `test_generic_rules_apply_during_generation` (생성 경로에서 일반 룰 적용 보증) | 통과 |

## 결정 사항

- **하드 필터 = 와일드카드, 소프트 점수 = 정확 매치만**: 필터는 "적용 가능한가"
  (일반 룰은 어디에나 적용), 랭킹은 "얼마나 특화됐나" (fastapi 전용 패턴이 일반
  패턴보다 위). 두 함수의 의미를 docstring에 명시해 재발 방지.
- 이 버그는 단위 테스트가 아닌 **실제 MCP 왕복 데모**에서만 드러났다
  (기존 테스트는 모든 엔트리가 필터 키를 선언한 케이스만 다룸). 실동작 스모크의
  가치 → 데모 스크립트를 향후 E2E 테스트로 승격 검토 (백로그).

## 검증 결과

- `pytest`: **90 passed** (기존 88 + 회귀 2)
- 실동작 재검증 (`acode serve` ↔ 실제 MCP 클라이언트 ↔ 실제 claude CLI):
  - 수정 전: checked_rules `[]`, 랭킹 1위가 무관한 패턴 (overlap 1.0 오염)
  - 수정 후: checked_rules 7개 룰 전부, `py-pattern-fastapi-route` score 1.000 1위,
    생성 코드가 수리 0회로 통과 (시드 패턴의 async/Depends/model_validate 형태 재현)
  - review_code: 의도적 위반 5종(PascalCase/mutable default/print/bare except/docstring)
    전부 기계 검출 → LLM 수정본 재검증 `fix_verified: true`

## 다음 단계 / 핸드오프

- (백로그) MCP 실왕복 데모를 LLM 목킹 가능한 E2E 테스트로 승격
