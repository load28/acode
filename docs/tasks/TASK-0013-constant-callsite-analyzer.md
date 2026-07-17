# TASK-0013: constant-callsite 분석기 — 파생 타입 파라미터에는 상수 멤버로 호출

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/optional-variant-vec-dl42zf |
| **시작일** | 2026-07-16 |
| **완료일** | 2026-07-16 |
| **작업 세션** | 1 |

## 목표

TASK-0011/0012에서 룰들이 as const 객체 + 파생 타입
(`type T = typeof Obj[keyof typeof Obj]`)을 제안하도록 했으니, 그 짝으로
**호출부 강제**를 추가한다 (사용자 요청): 파생 타입으로 선언된 파라미터에
raw 문자열 리터럴을 넘기면 잡고, 상수 멤버 참조(`Align.Left`)를 제안한다.
상수로 호출해야 값 변경/리네임이 객체 한 곳에서 끝난다.

증거 사슬 (전부 파일 내에서 확인될 때만 발화):
1. `const X = { K: 'v', ... } as const` — 식별자 키 + 문자열 값만
2. `type T = typeof X[keyof typeof X]` — 두 typeof 대상이 같은 X
3. 파일 내 함수 선언의 파라미터가 `T`로 주석됨
4. 그 함수의 직접 호출에서 해당 인자가 X의 값과 일치하는 raw 문자열 리터럴

**DoD**: 발화/침묵 케이스 테스트, tsx 상속, 시드 자가 검증, 전체 pytest 통과.

## 진행 상황

- [x] 그래머 확인 — 파생 alias는 `lookup_type(type_query(id X), index_type_query(type_query(id X)))`
- [x] `analyzers.py` — `constant-callsite` 구현 + 등록
- [x] `conventions/typescript.json` — `ts-prefer-constant-callsite` 시드
- [x] 테스트 + pytest + 문서/INDEX + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | `constant_callsite` + 헬퍼 2개(`_as_const_string_members`: as const 객체 → {값→멤버} 매핑, `_derived_union_aliases`: 파생 alias → 객체 매핑) 추가, `ANALYZERS` 등록 | 검증됨 |
| `conventions/typescript.json` | 수정 | `ts-prefer-constant-callsite` 시드 (총 20건) — bad: `alignLabel('left')`, good: `alignLabel(Align.Left)` | 자가 검증 통과 |
| `tests/test_constant_callsite.py` | 생성 | 발화(raw 리터럴, 호출별, 객체명≠타입명, 다중 파생 파라미터), 침묵(멤버 참조, 집합 밖 리터럴, 변수 인자, plain alias, as const 없음, typeof 대상 불일치, 미지 함수), tsx, 시드 | 13 passed |

## 결정 사항

- **상수 객체는 식별자 키 + 문자열 값만 인정** — 문자열 키는 `X.Key` 멤버
  접근 제안이 성립하지 않고, 숫자 값 지원은 후속 여지로 남김.
- **객체 값에 없는 리터럴은 침묵** — 어차피 컴파일 에러(타입 불일치),
  컴파일러의 몫.
- **export 함수도 검사** — stringly-literal-param과 달리 닫힌 집합 증명이
  필요 없고, 파일 내 호출부 각각이 독립적인 위반이므로 면제 불필요.

## 검증 결과

- `pytest` 전체: **179 passed, 3 skipped** (신규 13), 회귀 0
- 시드 임포트: 20건 자가 검증 통과
- 스모크 (시드 룰 16건 전체 적용): `alignLabel('left')`만 발화 —
  "raw literal 'left' passed where the parameter is typed 'Align' —
  reference the constant `Align.Left` so value changes stay in one
  place". `alignLabel(Align.Right)`는 통과, as const 객체 자체는 다른
  룰(as-const-candidate 등)에도 안 걸림.

## 다음 단계 / 핸드오프

- 사용 환경에서 `acode import conventions/typescript.json --replace`
  재실행 필요 (20건).
- 한계(의도): 파일 내 증거 사슬만 봄 — import된 파생 타입/함수는 미검사.
  숫자 값 상수 객체(`{Low: 1}`)는 미지원(후속 여지). 변수 선언
  초기화(`const a: Align = 'left'`)는 호출부가 아니라 범위 밖 — 필요 시
  후속 태스크로 확장.
- 이제 흐름 완성: stringly-literal-param(객체 만들라) →
  duplicate-literal-union(집합 한 곳으로) → constant-callsite(호출은
  상수로).
