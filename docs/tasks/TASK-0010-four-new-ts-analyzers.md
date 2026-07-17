# TASK-0010: TS 분석기 4종 추가 — boolean-variant-bag / stringly-literal-param / duplicate-literal-union / as-const-candidate

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/optional-variant-vec-dl42zf |
| **시작일** | 2026-07-16 |
| **완료일** | 2026-07-16 |
| **작업 세션** | 1 |

## 목표

TASK-0008/0009에서 확립한 **증거 기반 결정적 analysis 룰** 철학을 따르는
분석기 4종을 추가한다. 공통 원칙: 선언 형태만 보고 단정하지 않고, 파일 내에서
확증 증거를 수집한 뒤에만 발화하며, 반증 증거가 있으면 침묵한다.

1. **boolean-variant-bag** — 상호 배타적 boolean 플래그 여러 개를 한
   인터페이스에 담는 패턴 (`isLoading`/`isError`...) → status union 유도.
   증거: 파일 내 사용처들에서 동시에 true인 플래그가 한 번도 없고, 서로 다른
   플래그 ≥2개가 각각 단독 true로 등장.
2. **stringly-literal-param** — `string` 파라미터인데 파일 내 모든 호출처가
   닫힌 집합의 문자열 리터럴만 넘김 → 리터럴 union 유도.
   침묵: export된 함수(외부 호출처 안 보임), 비리터럴 인자, 간접 참조.
3. **duplicate-literal-union** — 같은 리터럴 union이 파일 내 여러 위치에
   인라인 반복 → named type alias 추출 유도. alias가 이미 있으면 이름 지목.
4. **as-const-candidate** — 모듈 레벨 무주석 const 객체 리터럴(리터럴 값만)이
   파일 내에서 한 번도 변이되지 않음 → `as const` 제안.

**DoD**: (a) 각 분석기가 발화/침묵 케이스를 정확히 가르고, (b) tsx 다이얼렉트
상속 동작, (c) 시드 컨벤션 4건이 자가 검증 통과, (d) 전체 pytest 통과.

## 진행 상황

- [x] 그래머 노드 형태 검증 (boolean predefined_type, call arguments, union 중첩, export_statement, delete/Object.assign)
- [x] `astcore/analyzers.py` — 분석기 4종 구현 + ANALYZERS 등록
- [x] `conventions/typescript.json` — analysis 룰 4건 시드
- [x] 테스트 4파일 + 전체 pytest
- [x] 실전 스모크 (tsx, 4개 분석기 동시 발화)
- [x] 문서/INDEX 갱신 + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | 분석기 4종 + 헬퍼(`_pair_value`, `_identifier_used_outside_calls`, `_is_module_level_const`, `_has_direct_mutation`) 추가, `ANALYZERS`에 등록. 기존 헬퍼(`_typed_object_literals`, `_static_object_keys`, `_union_leaves`) 재사용 | 검증됨 |
| `conventions/typescript.json` | 수정 | analysis 룰 4건 append: `ts-no-boolean-variant-bag`, `ts-prefer-literal-union-param`, `ts-no-duplicate-literal-union`, `ts-prefer-as-const-object` — 총 19건 | 자가 검증 통과 |
| `tests/test_boolean_variant_bag.py` | 생성 | 발화(usage/satisfies/as), 침묵(true 동시 발생, 동적 값, shorthand, 단독 true 1종, 사용처 없음, boolean 1개), tsx, 시드 자가 검증 | 10 passed |
| `tests/test_stringly_literal_param.py` | 생성 | 발화(닫힌 리터럴 집합, 다중 파라미터 독립 판정), 침묵(고유값 1, 비리터럴/누락 인자, export, 간접 참조, 호출 <2, 비string), tsx, 시드 | 11 passed |
| `tests/test_duplicate_literal_union.py` | 생성 | 발화(인라인 반복 각각, 기존 alias 이름 지목, 순서 무관), 침묵(단일 출현, 다른 집합, 비리터럴 멤버, alias 2개), tsx, 시드 | 9 passed |
| `tests/test_as_const_candidate.py` | 생성 | 발화(무변이 리터럴 const, export 포함, 혼합 프리미티브), 침묵(as const 기존, 주석 있음, 멤버/인덱스 쓰기, delete, Object.assign, 비리터럴 값, 빈 객체, spread, let, 함수 지역), tsx, 시드 | 16 passed |

## 결정 사항

- **boolean-variant-bag은 사용처 증거만 사용** — 선언부 신호(이름 접두사
  `is`/`has` 등)는 휴리스틱이라 배제. 플래그에 비리터럴 값을 대입하는 사용처가
  하나라도 있으면 배타성을 증명할 수 없으므로 침묵.
- **stringly-literal-param은 export 함수 제외** — 파일 밖 호출처를 볼 수 없어
  닫힌 집합을 증명할 수 없음. 함수 식별자가 호출 이외 위치에서 참조되면(콜백
  전달 등) 간접 호출 가능성 → 침묵. 호출 ≥2 + 고유 리터럴 ≥2 요구
  (1개 리터럴은 초기 사용 단계일 수 있어 오탐 위험 → 배제).
- **duplicate-literal-union은 alias 선언 자체는 발화 안 함** — 같은 집합의
  alias 2개는 의도적 별칭일 수 있음. 인라인(비-alias) 출현만, 집합이 파일 내
  총 ≥2회 등장할 때 발화. 집합 비교는 순서 무관(frozenset).
- **as-const-candidate는 모듈 레벨 + 리터럴 값만** — 함수 내부 지역 const는
  노이즈 위험으로 배제. 값에 식/호출이 섞이면 배제. 직접 변이(멤버/인덱스
  대입, 재대입, delete, Object.assign 1번째 인자)가 있으면 침묵. 별칭 경유
  변이나 함수 전달 후 변이는 미탐지 — 의도된 한계로 문서화.

- **shorthand/spread 프로퍼티는 boolean 증거에서 증명 불가로 처리** —
  `{ isLoading }`은 변수 전달, `{ ...base }`는 보이지 않는 플래그 설정
  가능성 → 둘 다 침묵 (구현 중 발견해 반영).
- **신규 pattern 시드는 추가 안 함** — 기존
  `ts-pattern-discriminated-view-model`(boolean 룰의 목표 형태),
  `ts-pattern-derived-record-keys`(as const 룰의 목표 형태)가 이미 커버.
- mcpserver 변경 불필요 — `add_convention`의 analyzer 파라미터가 레지스트리를
  동적 조회하므로 등록만으로 노출됨.

## 검증 결과

- `pytest` 전체: **166 passed, 3 skipped** (신규 46 포함: 10+11+9+16), 회귀 0
- 시드 임포트: 19건 전부 자가 검증 통과 (bad_example 검출 / good_example 통과)
- 실전 스모크 (tsx, 다이얼렉트 상속 경유, 시드 룰 15건 전체 적용): 4개 분석기
  동시 발화 — `STATUS_COLORS`(as const 후보), `PokemonCard`(배타 boolean 플래그
  isLoading/isFailed), `'ok'|'warn'|'bad'` 인라인 반복 2곳, `playCry(volume:
  string)`의 닫힌 호출 집합 `'high'|'low'` 정확히 지목. 정당한 코드는 침묵.

## 다음 단계 / 핸드오프

- 이 컨테이너에는 라이브 DB가 없어 재시드 안 함 — 사용 환경에서
  `acode import conventions/typescript.json --replace` 1회 필요 (19건).
- MCP 서버 사용 중이면 재연결로 신규 분석기 반영.
- 한계(의도된 범위): 네 분석기 모두 파일 내 증거만 봄. stringly-literal-param은
  function_declaration만 대상(화살표 함수 상수 미지원 — 필요 시 후속),
  as-const-candidate는 별칭/피호출자 경유 변이 미탐지.
