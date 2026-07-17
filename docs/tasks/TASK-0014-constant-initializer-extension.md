# TASK-0014: constant-callsite 확장 — 변수 초기화·파라미터 기본값도 상수로

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/optional-variant-vec-dl42zf |
| **시작일** | 2026-07-16 |
| **완료일** | 2026-07-16 |
| **작업 세션** | 1 |

## 목표

TASK-0013의 `constant-callsite` 분석기를 확장한다 (사용자 요청): 호출 인자뿐
아니라 **파생 타입으로 주석된 변수 초기화**(`const a: Align = 'left'`)와
**파라미터 기본값**(`align: Align = 'left'`)의 raw 리터럴도 잡아
`Align.Left` 참조를 제안한다. 증거 사슬(객체 → 파생 alias → 주석 → 일치
리터럴)은 동일 — 사용 위치만 추가.

**DoD**: 변수/기본값 발화·침묵 테스트, 시드 자가 검증, 전체 pytest 통과.

## 진행 상황

- [x] 그래머 확인 — `variable_declarator`/`required_parameter` 모두 `type` + `value` 필드로 주석·초기값 접근 가능
- [x] `analyzers.py` — 초기화 스캔 추가 (+주석 판독 헬퍼로 중복 제거)
- [x] `conventions/typescript.json` — guideline/examples에 변수 초기화 반영
- [x] 테스트 + pytest + 문서/INDEX + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | `constant_callsite`에 초기화 스캔 추가 — `variable_declarator`/`required_parameter`/`optional_parameter`의 `type`+`value` 필드 검사, 사이트별 메시지("initializes a variable" / "a parameter default"). 주석 판독을 `_annotated_alias_name` 헬퍼로 추출해 호출부 스캔과 공유 | 검증됨 |
| `conventions/typescript.json` | 수정 | guideline을 "call arguments, variable initializers, and parameter defaults"로 확장, bad_example에 `const fallback: Align = 'right'` 추가, good_example은 멤버 참조 3형태 모두 시연 | 자가 검증 통과 |
| `tests/test_constant_callsite.py` | 수정 | 신규 6건 — 변수 초기화 발화(const/let), 멤버 초기화 통과, 무주석 침묵, 파라미터 기본값 발화/통과 | 19 passed |

## 결정 사항

- **같은 룰/분석기(`ts-prefer-constant-callsite`/`constant-callsite`)를 확장**
  — 별도 룰로 쪼개지 않음. "파생 타입 자리에 raw 리터럴 금지"라는 하나의
  원칙이고, 등록된 분석기 이름을 바꾸면 기존 저장 룰이 깨지므로 이름 유지.
- 파라미터 기본값도 포함 — 변수 초기화와 같은 형태(주석+리터럴)의 초기화라
  함께 처리. `let`도 `variable_declarator`라 자동 포함.

## 검증 결과

- `pytest` 전체: **185 passed, 3 skipped** (신규 6), 회귀 0
- 시드 임포트: 20건 자가 검증 통과
- 스모크 (시드 룰 전체 적용): 한 파일에서 3개 사이트 동시 발화 확인 —
  변수 초기화(`Align.Right` 제안), 파라미터 기본값(`Align.Left`),
  호출 인자(`Align.Left`). `alignLabel(Align.Right)`는 통과.

## 다음 단계 / 핸드오프

- 사용 환경에서 `acode import conventions/typescript.json --replace`
  재실행 필요.
- 남은 미커버 사이트(필요 시 후속): 객체 리터럴 프로퍼티 값
  (`{ align: 'left' }`가 파생 타입 프로퍼티에 대입되는 경우), return 문,
  재대입(`current = 'left'`). 모두 같은 증거 사슬로 확장 가능.
