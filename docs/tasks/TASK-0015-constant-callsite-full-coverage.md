# TASK-0015: constant-callsite 전체 커버리지 — 재대입·return·프로퍼티·배열·화살표/메서드·숫자

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/optional-variant-vec-dl42zf |
| **시작일** | 2026-07-16 |
| **완료일** | 2026-07-16 |
| **작업 세션** | 1 |

## 목표

TASK-0014에서 남겨둔 미커버 사이트를 전부 추가한다 (사용자 요청):

1. **재대입** — `cur = 'right'` (파생 타입으로 선언된 변수)
2. **return 문** — 반환 타입이 파생 타입인 함수의 raw 리터럴 반환
   (화살표 표현식 본문 포함, 중첩 함수의 return은 소유자에게만 귀속)
3. **객체 프로퍼티 값** — interface/type(object_type)의 프로퍼티가 파생
   타입일 때 `: T` / `satisfies T` / `as T` 객체 리터럴의 해당 값
4. **배열/제네릭** — `Align[]`, `Array<Align>`, `ReadonlyArray<Align>`,
   `Set<Align>` 자리의 배열 리터럴 요소
5. **화살표 함수·함수 표현식·메서드** — const에 바인딩된
   화살표/함수 표현식의 호출, 메서드 호출(`w.render('left')`)
6. **숫자 값 상수 객체** — `const Level = { Low: 1 } as const` → `setLevel(1)`

**계속 제외 (설계 원칙)**: 파일 경계(cross-file) — 결정적 분석기가 타입
체커 없이 import 추적을 하면 오탐 위험. 파일 내 증거 사슬 원칙 유지.

**DoD**: 사이트별 발화·침묵 테스트, 기존 테스트 회귀 0, 시드 자가 검증,
전체 pytest 통과.

## 진행 상황

- [x] 그래머 확인 — `return_type`/`body` 필드(함수 3종), `array_type`,
  `generic_type(type_arguments)`, `assignment_expression`, method 호출 형태
- [x] `analyzers.py` — constant_callsite 재구성 (슬롯 판독/리터럴 매칭 공용화)
- [x] `conventions/typescript.json` — guideline 확장
- [x] 테스트 + pytest + 문서/INDEX + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | `constant_callsite` 재구성 — 공용 헬퍼: `_annotation_slot`(scalar/array 슬롯 판독, `T[]`/`Array<T>`/`ReadonlyArray<T>`/`Set<T>`), `_literal_key`(string/number 정규화), `_own_returns`(중첩 함수 제외 return 수집), `report()`(사이트별 문구 + 배열 요소 전개). `_as_const_string_members`→`_as_const_members`로 개명·확장(숫자 값, (kind,value) 키). 수집: 함수 선언/화살표·함수표현식 const/메서드(시그니처 충돌 시 배제), 변수 슬롯(재선언 충돌 시 배제), interface/type(object_type) 프로퍼티 슬롯 | 검증됨 |
| `conventions/typescript.json` | 수정 | guideline에 전체 사이트 목록 + 숫자 멤버 명시 | 자가 검증 통과 |
| `tests/test_constant_callsite.py` | 수정 | 신규 13건 — 재대입(발화/모호 재선언 침묵), return(문/화살표 표현식 본문/중첩 함수 귀속), 배열(`Align[]`/`Array<Align>`), 프로퍼티(`: Config`/`satisfies`), 화살표 호출, 메서드 호출(발화/시그니처 충돌 침묵), 숫자 멤버 | 32 passed |

## 결정 사항

- **모호성은 침묵으로**: 같은 이름의 메서드가 다른 파라미터 매핑으로 여러 번
  정의되면 그 이름 전체를 배제(어느 클래스 인스턴스인지 추적 불가). 같은
  이름의 변수가 다른 슬롯 타입으로 재선언돼도 동일.
- **컨테이너는 1겹만**: `Align[]`/`Array<Align>`/`Set<Align>`까지.
  `Record<string, Align>`, 튜플, 중첩 제네릭은 범위 밖 (필요 시 후속).
- **재대입은 단순 `=`만**: `||=`/`??=` 등 논리 대입은 드물고 의미가 달라 제외.
- 상수 객체 값에 string/number 혼합 허용; boolean 등 다른 타입이 섞이면
  객체 전체 배제(기존 보수 원칙 유지).

## 검증 결과

- `pytest` 전체: **198 passed, 3 skipped** (신규 13, constant-callsite 총 32), 회귀 0
- 시드 임포트: 20건 자가 검증 통과
- 스모크 (tsx, 시드 룰 전체): 한 파일에서 7개 사이트 동시 발화 — 재대입,
  배열 요소('Align[]' 표기, 멤버 참조 요소는 통과), 화살표 표현식 본문
  return, 객체 프로퍼티(문자열 `Align.Left` + 숫자 `Level.High`), 메서드
  return, 메서드 호출 인자. 정당한 사용(멤버 참조)은 전부 침묵.

## 다음 단계 / 핸드오프

- 사용 환경에서 `acode import conventions/typescript.json --replace` 재실행.
- 남은 범위 밖(의도): cross-file(설계 원칙), `Record<string, T>` 값 위치,
  튜플/중첩 제네릭, 논리 대입(`||=` 등), 구조분해 재대입. 수요가 생기면
  후속 태스크로.
