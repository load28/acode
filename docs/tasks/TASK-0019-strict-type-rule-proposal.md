# TASK-0019: 강한 타입 제약 룰 제안 (10종 — 쿼리 룰 6 + 분석기 룰 2 + 패턴 2)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/typescript-strict-type-rules-hcq34x` |
| **시작일** | 2026-07-19 |
| **완료일** | 2026-07-19 |
| **작업 세션** | 1개 세션 |

## 목표

"타입을 강하게 제약한다"는 테마로 기존 TS 룰셋(23건)과 TASK-0017 백로그
(switch-exhaustiveness 등 4종 + no-default-export)에 **없는** 신규 룰 후보를
제안한다. TASK-0017과 같은 형식: 구현이 아니라 제안이 결과물이며, 쿼리 룰은
전부 실제 `RuleEngine`에서 검증한다.

Definition of Done:
- 후보 룰마다 근거·good/bad 예제·구현 방식·오탐 가드를 명시
- 단순 쿼리 룰은 실제 `RuleEngine`에서 컴파일 + 양성/음성 예제 스모크 통과 확인
- 엣지 케이스(어노테이트된 화살표, 인터페이스 빈 몸체, 구조 분해 파라미터 등) 실측
- 배제한 후보와 배제 이유 기록

## 진행 상황

- [x] 기존 `conventions/typescript.json` 23건 + TASK-0017 백로그 검토 (중복 배제)
- [x] 후보 발굴 및 선별 (채택 10, 배제 5)
- [x] 쿼리 룰 6종 엔진 스모크 검증 (6/6 통과) + 엣지 케이스 9건 실측
- [x] 제안서 작성 (이 문서)

---

## 제안 A — 단순 쿼리 룰 6종 (엔진 검증 완료, 즉시 구현 가능)

아래 6종은 실제 `RuleEngine`으로 쿼리 컴파일 + bad 예제 양성 / good 예제 음성을
확인했다 (검증 결과 절 참조). 채택 시 `conventions/typescript.json`에 항목만
추가하면 된다. 테마: **컴파일러 우회로 봉쇄(A1)** → **넓은 타입 금지(A2·A5)**
→ **암묵 타입 금지(A3·A4)** → **모듈 경계 불변성(A6)**.

### A1. `ts-no-angle-bracket-assertion` — `<T>expr` 단언 금지 — **우선순위 1**

- **근거**: TASK-0018에서 구현된 `ts-no-type-assertion`은 `as_expression`만
  잡는다. 같은 단언을 옛 문법 `<HTMLInputElement>el`로 쓰면 별개 노드
  (`type_assertion`)라 그대로 통과한다 — 기존 룰의 마지막 우회로. `.tsx`에서는
  JSX와 충돌해 아예 못 쓰는 문법이므로 금지해도 잃는 표현력이 없다.
- **구현**: `forbid` / `(type_assertion) @bad`.
- **오탐 가드**: 제네릭 호출 `f<T>(x)`은 `type_arguments`로 파싱되어 매치되지
  않는다. `<const>expr` 형태의 const 단언은 **의도적으로 함께 금지** — 저장소
  전체가 `as const` 철자를 표준으로 쓰므로 철자를 하나로 통일한다 (결정 사항).
- bad: `const el = <HTMLInputElement>document.getElementById('x');`
- good: `if (el instanceof HTMLInputElement) { ... }` (내로잉으로 증명)

### A2. `ts-no-wrapper-object-types` — 래퍼 객체 타입 금지 — **우선순위 2**

- **근거**: `String`/`Number`/`Boolean`/`Symbol`/`BigInt`는 원시 타입의 박싱
  래퍼라 `s: String = new String('x')` 같은 비원시 값을 허용하고 원시와의
  비교·할당 시맨틱이 어긋난다. `Object`는 사실상 모든 비-nullish 값,
  `Function`은 시그니처 검사가 전혀 없는 호출 가능 값 — 전부 "타입을 쓰긴
  했지만 아무것도 제약하지 않는" 구멍이다. `ts-no-any`의 직선 연장.
- **구현**: `forbid` /
  `((type_identifier) @bad (#match? @bad "^(String|Number|Boolean|Object|Symbol|BigInt|Function)$"))`
- **오탐 가드**: `type_identifier`는 타입 위치에만 등장하므로 값 위치의
  `String(42)` 변환 함수·`new String('x')` 생성자 호출은 매치되지 않음을 실측
  확인. `Array<Function>`처럼 제네릭 인자 속에 숨어도 잡힌다 (실측 1건).
- **제안 메시지**: 원시 소문자 타입(`string`/`number`/…), `Function` 대신
  구체 시그니처(`(x: T) => R`), `Object` 대신 `object` 또는 구체 형태.
- bad: `function f(s: String, cb: Function): Object { ... }` (3건 검출)
- good: `function f(s: string, cb: () => void): { s: string } { ... }`

### A3. `ts-no-implicit-any-param` — 어노테이션 없는 파라미터 금지 — **우선순위 3**

- **근거**: `ts-no-any`는 **명시적** `any`만 잡는다. 함수 선언·클래스 메서드의
  파라미터는 문맥 타입(contextual typing)이 없어서 어노테이션을 빼면 그냥
  암묵 `any`다 — 명시 `any` 금지를 우회하는 가장 흔한 실수 경로. 엔진은
  tsconfig 없이 코드 조각만 보므로 `noImplicitAny` 여부와 무관하게 리뷰
  시점에 제약이 보이게 하는 가치가 있다.
- **구현**: `forbid` / 대체 패턴 리스트 —
  `function_declaration`·`method_definition`의 `formal_parameters` 안
  `required_parameter`/`optional_parameter` 중 `!type`(타입 필드 부재)인 것.
  ```
  [(function_declaration parameters: (formal_parameters (required_parameter pattern: (identifier) !type) @bad))
   (function_declaration parameters: (formal_parameters (optional_parameter pattern: (identifier) !type) @bad))
   (method_definition parameters: (formal_parameters (required_parameter pattern: (identifier) !type) @bad))
   (method_definition parameters: (formal_parameters (optional_parameter pattern: (identifier) !type) @bad))]
  ```
- **오탐 가드 (전부 실측)**: 화살표 함수는 **의도적으로 제외** — 콜백 위치
  (`[1,2].map((n) => n * 2)`)에서는 문맥 타입이 파라미터를 정확히 추론하므로
  어노테이션 강제가 오히려 소음이다 (0건 확인). 구조 분해 파라미터
  (`function f({ a })`)는 `pattern: (identifier)` 제약으로 침묵 (0건 확인 —
  잡으려면 별도 확장). 생성자 프로퍼티(`constructor(private x)`)는 잡힌다
  (1건 확인 — 여기도 암묵 any이므로 올바른 발화).
- bad: `function add(a, b?) { ... }` / `class Store { set(key) { ... } }`
- good: `function add(a: number, b?: number): number { ... }`

### A4. `ts-explicit-export-return-type` — export 함수는 반환 타입 명시 — **우선순위 4**

- **근거**: 모듈 경계가 추론에 의존하면 구현 리팩터링이 공개 API 타입을
  소리 없이 바꾼다 (`return` 하나 고쳤는데 반환 타입이 `string`→
  `string | undefined`로 번져도 해당 파일은 컴파일 성공, 소비자 쪽에서 터짐).
  명시 반환 타입은 경계를 계약으로 고정해 변경을 선언 지점에서 감지하게
  한다. typescript-eslint `explicit-module-boundary-types`의 결정적 부분집합.
- **구현**: `forbid` / 대체 패턴 리스트 —
  ```
  [(export_statement (function_declaration !return_type) @bad)
   (export_statement (generator_function_declaration !return_type) @bad)
   (export_statement (lexical_declaration (variable_declarator !type value: (arrow_function !return_type)) @bad))]
  ```
- **오탐 가드 (전부 실측)**: 변수 어노테이션으로 이미 타입이 있는
  `export const f: (n: number) => number = (n) => n * 3`은 declarator의
  `!type` 가드로 제외 (0건 확인 — 초안에는 이 가드가 없어 오탐이었고 수정함,
  결정 사항 참조). 비-export 함수·비함수 export(`export const total = 1`)는
  비발화. `export default function`도 잡힌다 (실측 1건).
- bad: `export function load(id: number) { ... }` /
  `export const twice = (n: number) => n * 2;`
- good: `export function load(id: number): string { ... }`

### A5. `ts-no-empty-object-type` — `{}` 타입 금지 — **우선순위 5**

- **근거**: 타입 위치의 `{}`는 "빈 객체"가 아니라 "null/undefined 빼고 전부"
  다 — `const x: {} = 42`가 컴파일된다. 넓은 타입 계열(`any`·`Object`)과 같은
  구멍인데 생김새 때문에 의도가 오독되는 최악의 케이스.
- **구현**: `forbid` / `((object_type) @bad (#match? @bad "^\\{\\s*\\}$"))`
  — 텍스트 술어로 "자식 없음"을 표현 (TASK-0017에서 `no-empty-interface`를
  배제했던 기술적 이유를 우회하는 방법).
- **오탐 가드 (전부 실측)**: `interface Empty {}`의 몸체는 `interface_body`
  노드라 매치 안 됨 (0건 확인 — 빈 인터페이스는 이 룰의 대상이 아님). 값
  위치의 `const empty = {}`는 `object` 노드라 무관 (0건 확인). 제네릭 제약
  `T extends {}`는 **잡힌다** (1건 실측) — "null/undefined 제외" 의도라면
  `T extends NonNullable<unknown>`이 정확한 철자이므로 의도된 발화로 문서화.
- **제안 메시지**: 아무 값이나 의도면 `unknown`, 비-nullish 의도면
  `NonNullable<unknown>`, 객체 의도면 `object` 또는 구체 형태.
- bad: `function keep(value: {}): {} { ... }` (2건 검출)
- good: `function keep(value: NonNullable<unknown>): object { ... }`

### A6. `ts-no-export-let` — `export let` 금지 — **우선순위 6**

- **근거**: export된 `let`은 모듈 밖 어디서든 시점 불명으로 재대입될 수 있는
  전역 가변 상태라, 컴파일러가 임포트 쪽에서 내로잉을 유지할 수 없고
  (`if (flag)` 검사 후에도 다른 모듈이 바꿨을 수 있음) 리터럴 타입도 넓혀진다.
  `export const`는 값과 타입 모두 고정한다. `as const` 파생 타입 철학(단일
  진실 공급원은 불변)의 모듈 경계 버전.
- **구현**: `forbid` / `(export_statement (lexical_declaration kind: "let") @bad)`
- **오탐 가드**: 모듈 내부 `let`(재대입 필요한 지역 상태)은 비발화 (음성 확인).
  재대입이 정말 필요한 export는 getter 함수 또는 객체 프로퍼티로 감싸는 형태를
  메시지에서 제안.
- bad: `export let counter = 0;` / good: `export const counter = 0;`

## 제안 B — 분석기 룰 2종 (각각 태스크 1개 규모, 설계 스케치)

TASK-0017 제안 B(4종 — 백로그에 있음)와 중복 없는 신규 2종. 같은 원칙:
**파일 안에서 증거 사슬이 완결될 때만 발화**, 오탐 0 지향.

### B1. `ts-prefer-satisfies-annotation` (analyzer: `satisfies-candidate`) — **우선순위 1**

- **근거**: `const config: Config = {...}`처럼 리터럴 초기화식에 넓은 타입을
  어노테이트하면 컴파일러가 리터럴 정보를 버린다 — `config.port`가 `3000`이
  아니라 `number`가 되고, 키 유니온 파생(`keyof typeof`)도 넓어진다.
  `const config = {...} satisfies Config`는 **검사는 유지하면서 추론은
  리터럴로 보존** — 이 저장소의 as const 파생 타입 생태계와 정확히 합치하는
  TS 4.9+ 기능. `record-key-inference` 분석기(Record<string,V> 전용)의 일반화.
- **검출**: 모듈 레벨 `const`가 (1) 명명 타입 어노테이션 + (2) 객체 리터럴
  초기화식(값 전부 리터럴)이고 (3) 파일 안에서 그 상수의 키/값이 파생 타입
  (`keyof typeof`/`typeof ... [keyof ...]`)이나 리터럴 좁힘에 쓰이는 흔적이
  있으면 위반. (3)이 없으면 어노테이션이 의도일 수 있으므로 침묵 (넓혀도
  잃는 게 없는 경우까지 발화하면 소음).
- **제안 메시지**: `satisfies Config`로 이동 + 필요 시 `as const satisfies Config` 조합 제시.

### B2. `ts-no-single-use-type-param` (analyzer: `single-use-type-param`) — **우선순위 2**

- **근거**: 시그니처에서 딱 한 번만 등장하는 제네릭 타입 파라미터
  (`function log<T>(value: T): void`)는 아무것도 연결하지 않는다 — 제네릭의
  의미는 두 지점(파라미터↔반환, 파라미터↔파라미터)을 같은 타입으로 묶는 것.
  한 번 쓰인 `<T>`는 `unknown`과 동일한 제약 강도인데 제네릭처럼 보여서
  강타입이라는 착시를 준다. typescript-eslint `no-unnecessary-type-parameters`
  의 결정적 부분집합.
- **검출**: `function_declaration`/`method_definition`의 `type_parameters` 각
  파라미터 이름이 파라미터 타입+반환 타입 텍스트에서 등장하는 횟수를 센다.
  1회면 위반. `extends` 제약에만 쓰인 경우(`<T extends Base>(x: T)` — 반환에
  없으면 2회 미만)는 제약 반환 목적일 수 있어 침묵. 함수 본문에서 타입
  인자로 재사용(`new Map<T, ...>`)되면 침묵 — 본문 등장도 카운트에 포함.
- **제안 메시지**: 해당 위치를 `unknown`(또는 제약 타입)으로 직접 표기.

## 제안 C — 패턴 2종 (기계 룰 없음, kind: "pattern")

### C1. `ts-pattern-branded-type` — ID는 브랜드 타입으로

- **근거**: `type UserId = string` 별칭은 구조적으로 그냥 `string`이라
  `orderId`를 `userId` 자리에 넣어도 컴파일된다. 브랜드(명목) 타입은 구조적
  타입 시스템 안에서 서로 다른 ID 공간을 컴파일 타임에 분리하는 표준 기법 —
  "타입을 강하게"의 대표 패턴인데 현재 패턴 카탈로그에 없다.
- **good_example** 스케치:
  ```ts
  declare const UserIdBrand: unique symbol;
  export type UserId = string & { readonly [UserIdBrand]: never };

  export function toUserId(raw: string): UserId {
    if (!/^u_[0-9a-z]{8}$/.test(raw)) {
      throw new Error(`invalid user id: ${raw}`);
    }
    return raw as UserId;  // 생성자 함수 안 한 곳만 단언 허용점
  }
  ```
- **주의**: 생성자 함수 내부의 `as UserId` 한 줄은 `ts-no-type-assertion`과
  충돌한다 — 채택 시 이 지점을 `@ts-expect-error` 사유 방식으로 뚫을지, 룰에
  브랜드 생성자 예외를 둘지 결정 필요 (구현 태스크에서 판단).

### C2. `ts-pattern-unknown-boundary` — 경계 입력은 `unknown`으로 받아 파싱

- **근거**: `JSON.parse`·환경변수·네트워크 응답 등 외부 입력을 `any`로 받으면
  이후 전 구간이 비검사다. `unknown`으로 받고 타입 가드로 **파싱해서 증명**
  하는 "parse, don't validate" 형태를 표준 패턴으로 제시 — `ts-no-any`
  가이드라인("use unknown + narrowing")의 실행 예제이자
  `ts-pattern-const-object-enum`의 가드와 연결되는 조각.
- **good_example** 스케치: `unknown` → 필드별 `typeof` 검사 → 판별 유니온
  `Result`(기존 `ts-pattern-result-type`)로 반환.

## 배제한 후보 (다음 세션이 같은 검토를 반복하지 않도록)

| 후보 | 배제 이유 |
|---|---|
| `no-string-index-signature` (`[key: string]: V` 금지) | 진짜 열린 맵에는 정당한 문법. 닫힌 키 집합인 경우는 이미 `ts-no-wide-record-key` 분석기가 증거 기반으로 잡는다 — 쿼리 룰로 일괄 금지하면 오탐 |
| `object` 소문자 타입 금지 | "비-원시 전부"가 실제 의도인 경우(WeakMap 키 등)가 있어 `Object`·`{}`와 달리 정당 사용이 흔함 |
| tsconfig strict 플래그 강제 (`noUncheckedIndexedAccess` 등) | 룰 엔진은 코드 AST 검사기라 설정 JSON은 대상 밖. 문서 가이드라인감이지 룰감이 아님 |
| `no-optional-parameter` (옵셔널 파라미터 금지) | 오버로드 대체로 정당한 경우가 대부분. 변형 가방 문제는 이미 `ts-no-optional-variant-bag`이 증거 기반으로 잡음 |
| `prefer-readonly-*` 계열 | TASK-0017에서 배제한 이유 그대로 — 변이 여부 판정에 파일 밖 타입 정보 필요 |

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `docs/tasks/TASK-0019-strict-type-rule-proposal.md` | 생성 | 강한 타입 제약 룰 10종 제안서 (이 문서) | 작성 완료 |
| `docs/tasks/INDEX.md` | 수정 | TASK-0019 등록, 마지막 번호 갱신, 백로그에 구현 후속 등록 | 갱신됨 |
| (scratchpad) `smoke_strict_proposals.py` | 생성 | 제안 A 6종을 실제 RuleEngine으로 검증하는 스모크 + 엣지 9건 — 저장소 밖, 커밋 안 함 | 6/6 통과 |

## 결정 사항

- **결정**: TASK-0017 전례대로 모든 쿼리를 문서에 싣기 전에 실제 엔진에서 검증 /
  **이유**: 실제로 `ts-explicit-export-return-type` 초안이
  `export const f: (n: number) => number = (n) => n * 3`(변수 어노테이션으로
  이미 타입이 완비된 화살표)를 오탐했고, declarator에 `!type` 가드를 추가해
  0건으로 수정했다. 검증 없이 실었으면 구현 세션이 밟았을 함정.
- **결정**: `!type`·`!return_type` 같은 **부정 필드 문법이 이 엔진의
  tree-sitter 쿼리에서 동작함을 확인** / **이유**: "필드 부재" 검사는 지금까지
  룰셋에 전례가 없었다. 이번 스모크로 확인됨 — 이후 룰 작성 시 사용 가능한
  도구로 기록.
- **결정**: A1에서 `<const>expr` 형태의 const 단언도 예외 없이 금지 /
  **이유**: `ts-no-type-assertion`이 `as const`만 예외로 두는 것과 표면상
  비대칭이지만, 저장소 표준 철자는 `as const` 하나다. 같은 의미의 두 철자를
  허용하면 `duplicate-literal-union`류 룰들이 지키는 "한 가지 스타일" 원칙과
  어긋난다.
- **결정**: A3에서 화살표 함수 파라미터는 대상 제외 / **이유**: 콜백 위치의
  화살표는 문맥 타입이 파라미터를 정확히 추론한다 — 여기까지 강제하면 이미
  강하게 타입된 코드에 소음을 얹는다. 문맥 타입이 없는
  `function`/`method`만이 암묵 any의 실제 발생 지점.
- **결정**: A5에서 `T extends {}` 발화는 의도된 동작으로 유지 / **이유**:
  "null/undefined 제외" 의도의 정확한 철자는 `NonNullable<unknown>`이고,
  `{}`는 그 의도조차 오독되는 표기라 예외를 둘 근거가 약하다. 실측으로 발화
  사실을 확인하고 문서화해 둠.
- **결정**: 분석기 룰(B)·패턴(C)은 설계 스케치만, 구현하지 않음 / **이유**:
  TASK-0017 전례 — 이 태스크의 결과물은 제안. C1의 브랜드 생성자 단언 예외
  문제는 구현 태스크에서 결정할 사항으로 명시해 둠.

## 검증 결과

```
$ python smoke_strict_proposals.py   # scratchpad, RuleEngine 직접 호출
PASS ts-no-angle-bracket-assertion: bad=1 (expected 1), good=0
PASS ts-no-wrapper-object-types: bad=4 (expected 4), good=0
PASS ts-no-empty-object-type: bad=2 (expected 2), good=0
PASS ts-explicit-export-return-type: bad=3 (expected 3), good=0
PASS ts-no-export-let: bad=1 (expected 1), good=0
PASS ts-no-implicit-any-param: bad=3 (expected 3), good=0

6/6 candidates passed
```

엣지 케이스 실측 (본문 오탐 가드 절의 근거):

```
annotated-arrow flags: 0 (want 0)        # !type 가드 적용 후
unannotated flags: 2 (want 2)            # export default function 포함
interface Empty {}: 0 (want 0)
T extends {}: 1 (documented: 1)
type A = { }: 1 (want 1)
destructured param: 0 (silent by design: 0)
ctor property: 1 (want 1)
Array<Function>: 1 (want 1)
new String call: 0 (value position, out of scope)
```

기존 테스트는 건드린 코드가 없어 영향 없음 (이 태스크는 문서만 추가).

## 다음 단계 / 핸드오프

1. 채택할 룰을 정하면 제안 A 6종은 태스크 1개로 묶어
   `conventions/typescript.json` 추가 + good/bad 예제 기반 테스트 작성
   (쿼리는 이 문서 것을 그대로 사용 가능 — 이미 엔진 검증됨. TASK-0018이
   TASK-0017 제안 A를 구현한 방식과 동일하게 진행하면 된다).
2. 제안 B는 우선순위대로 태스크 1개씩 (B1 `satisfies-candidate` 권장 첫 착수 —
   `record-key-inference`의 증거 수집 코드 재사용도가 높다).
3. 제안 C 패턴 2종은 JSON 항목 추가만이라 A 구현 태스크에 합류 가능. 단 C1은
   브랜드 생성자의 `as` 단언 예외 정책을 먼저 결정할 것.
4. TASK-0017 백로그(분석기 4종 + no-default-export)는 그대로 유효 — 이 제안과
   중복 없음.
