# TASK-0009: Record 키 넓힘 검출 — record-key-inference 분석기

| | |
|---|---|
| **상태** | done |
| **브랜치** | main |
| **시작일** | 2026-07-13 |
| **완료일** | 2026-07-13 |
| **작업 세션** | 1 |

## 목표

사용자 컨벤션: "Record나 타입 선언 시 키/value가 추론 가능하면 그것을 활용한다.
`string` 키는 정말 열린(동적) 맵일 때만 쓴다."

키 집합이 컴파일 타임에 닫혀 있는데(`{ fire: ..., water: ... }`처럼 리터럴
키로 전부 열거) 타입을 `Record<string, V>`로 열어두는 선언-초기화 모순을
결정적으로 검출한다. TASK-0008에서 만든 `analysis` 룰 타입 위에 분석기만
추가한다.

**DoD**: (a) `Record<string, V>` / `{ [k: string]: V }` + 정적 키 리터럴
초기화가 검출되고, (b) 빈 리터럴·동적 키 쓰기·좁은 키 타입은 침묵하며,
(c) tsx 상속 동작, (d) 시드 자가 검증 통과, (e) 전체 pytest 통과.

## 진행 상황

- [x] 그래머 노드 형태 검증 (generic_type Record, index_signature, subscript 쓰기)
- [x] `astcore/analyzers.py` — `record-key-inference` 분석기 + 레지스트리 등록
- [x] `conventions/typescript.json` — analysis 룰 + as const 키 파생 패턴 시드
- [x] 테스트 + 전체 pytest
- [x] DB 재시드

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | `record-key-inference` 분석기: `_annotates_string_keyed_map`(Record<string,V> / 인덱스 시그니처 판별), `_static_object_keys`(닫힌 키 집합 판별 — 스프레드/계산 키/빈 리터럴이면 None), `_has_dynamic_key_write`(동적 키 쓰기 = 열린 맵 증거) | 검증됨 |
| `conventions/typescript.json` | 수정 | `ts-no-wide-record-key`(analysis 룰) + `ts-pattern-derived-record-keys`(패턴) 추가 — 27줄 append, 포맷 보존 | 자가 검증 통과 |
| `tests/test_record_key_inference.py` | 생성 | 위반(식별자/문자열 키, 인덱스 시그니처), 침묵(빈 리터럴·스프레드·계산 키·동적 쓰기·좁은 키·미초기화), 리터럴 키 쓰기는 면죄부 아님, tsx 상속, 시드 자가 검증 | 12 passed |

## 결정 사항

- **판정 = 선언과 초기화의 모순 (증거 기반, 결정적)**:
  - 어노테이션이 `Record<string, V>` 또는 인덱스 시그니처 `{ [k: string]: V }`
  - 초기값이 정적 키(식별자/문자열 리터럴)만으로 구성된 객체 리터럴, 키 ≥1
    (스프레드/계산 키가 하나라도 있으면 열린 집합일 수 있으므로 침묵)
  - 같은 파일에서 그 변수에 동적 키 쓰기(`m[expr] = ...`, expr이 문자열
    리터럴이 아님)가 있으면 진짜 동적 맵 → 침묵
- **value 넓힘은 기계 룰이 아닌 패턴으로**: 값까지 리터럴 추론을 받으려면
  어노테이션을 버리고 `as const` + `keyof typeof` 파생이 정답 — good_example로
  가르치고 generate/review 검색에 태운다. `Record<PokemonType, string>`처럼
  키만 좁으면 값이 string이어도 합법.
- 인터페이스/타입 별칭 프로퍼티의 `Record<string, V>`는 초기화 증거가 없어
  이번 범위에서 제외 (오탐 방지).

## 검증 결과

- `pytest` 전체: **125 passed, 2 skipped** (신규 12 포함), 회귀 0
- 실전 스모크: `Record<string, string>` + 리터럴 키 3개 → 위반 검출
  ("closed set (fire, water, grass) — derive the key type"), `as const` +
  `keyof typeof` 파생 + `Record<ColoredType, string>` 버전 → passed
- 시드 2건 인서트 자가 검증 통과, 라이브 DB 재시드 완료 (15 conventions)

## 다음 단계 / 핸드오프

- MCP 서버 재연결 필요 (`/mcp` reconnect) — analyzers.py 코드 반영
- 이번 범위 제외(의도): 인터페이스/타입 별칭 프로퍼티의 `Record<string, V>`
  (초기화 증거 없음), `Map<string, V>` (런타임 구조라 별개 논의),
  함수 파라미터 어노테이션 (호출부 증거 필요 — 크로스 파일)
