# TASK-0012: ts-no-duplicate-literal-union 제안도 as const 파생 타입 스타일로 변경

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/optional-variant-vec-dl42zf |
| **시작일** | 2026-07-16 |
| **완료일** | 2026-07-16 |
| **작업 세션** | 1 |

## 목표

TASK-0011(2번 룰)에 이어, `duplicate-literal-union` 분석기의 추출 제안도
plain type alias 대신 **as const 객체 + 파생 타입**
(`ts-no-enum`/`ts-pattern-const-object-enum` 컨벤션)으로 변경한다 (사용자
요청). 근거: 값이 상수 객체에 있으면 집합 변경이 한 곳에서 끝나고, 사용부가
`Size.Sm`처럼 상수를 참조하게 되어 리터럴 문자열 산재가 사라짐.

검출 로직 변경 없음. **기존 alias가 있는 케이스의 제안("use the existing
alias 'X'")은 유지** — 단일 진실 공급원이 이미 있으므로 그걸 쓰는 게 맞고,
그 alias의 스타일 마이그레이션까지 강제하는 건 이 룰의 범위 밖.

**DoD**: 추출 제안 메시지가 파생 타입 스타일을 안내하고, 시드 자가 검증
포함 전체 pytest 통과.

## 진행 상황

- [x] `analyzers.py` — 추출 제안 문구 변경 (alias 있는 분기는 유지)
- [x] `conventions/typescript.json` — guideline/message/good_example 갱신
- [x] 테스트 단언 갱신 + pytest + 문서/INDEX + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | alias 부재 분기의 제안을 "extract a named type alias"에서 as const 객체 + `typeof Obj[keyof typeof Obj]` 파생 union으로 변경, docstring 갱신. alias 존재 분기("use the existing alias 'X'")는 유지 | 검증됨 |
| `conventions/typescript.json` | 수정 | `ts-no-duplicate-literal-union`의 title/guideline/message/good_example을 파생 타입 스타일로 갱신 (good_example: `const Size = {...} as const` + 동명 파생 타입), 태그 `alias`→`as-const` | 자가 검증 통과 |
| `tests/test_duplicate_literal_union.py` | 수정 | 추출 제안 메시지에 `as const`/`keyof typeof` 포함 단언으로 교체 | 통과 |

## 결정 사항

- alias-존재 분기 유지: 반복 해소가 룰의 목적이므로 기존 alias 사용 안내가
  정답. plain alias → 파생 타입 마이그레이션 유도는 별도 관심사.

## 검증 결과

- `pytest` 전체: **166 passed, 3 skipped**, 회귀 0
- 시드 임포트: 19건 자가 검증 통과 (새 good_example에는 리터럴 union
  노드가 없어 룰 미발화 확인)
- 스모크: alias 부재 시 "hold the values in an `as const` object and
  derive the union (`type T = typeof Obj[keyof typeof Obj]`) so the set
  has one source of truth" 출력, alias 존재 시 기존 "use the existing
  alias 'X'" 유지 (기존 테스트로 커버)

## 다음 단계 / 핸드오프

- 사용 환경에서 `acode import conventions/typescript.json --replace` 재실행
  필요 (TASK-0010/0011과 동일).
- 이로써 값 집합을 다루는 룰 3종(ts-no-enum, ts-prefer-literal-union-param,
  ts-no-duplicate-literal-union)이 모두 같은 enum-대체 형태를 가리킴.
