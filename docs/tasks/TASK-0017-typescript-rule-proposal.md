# TASK-0017: 타입스크립트 신규 룰 제안 (10종 — 쿼리 룰 6 + 분석기 룰 4)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/typescript-rule-proposal-dgz09f` |
| **시작일** | 2026-07-19 |
| **완료일** | 2026-07-19 |
| **작업 세션** | 1개 세션 |

## 목표

기존 TS 룰셋(18건: 쿼리 룰 9 + analysis 룰 7 + pattern 4 상당)을 검토하고,
저장소 철학(결정적 검사, `as const` 파생 타입 중심, 낮은 오탐)에 맞는 **신규 룰
후보를 제안**한다. 구현이 아니라 제안이 결과물이다.

Definition of Done:
- 후보 룰마다 근거·good/bad 예제·구현 방식(쿼리 vs 분석기)·오탐 가드를 명시
- 단순 쿼리 룰은 실제 `RuleEngine`에서 컴파일 + 양성/음성 예제 스모크 통과 확인
- 배제한 후보와 배제 이유 기록

## 진행 상황

- [x] 기존 `conventions/typescript.json` 18건 + `rules.py`/`analyzers.py` 검토
- [x] 후보 발굴 및 선별 (채택 10, 배제 4)
- [x] 단순 쿼리 룰 6종 엔진 스모크 검증 (6/6 통과)
- [x] 제안서 작성 (이 문서)

---

## 제안 A — 단순 쿼리 룰 6종 (엔진 검증 완료, 즉시 구현 가능)

아래 6종은 실제 `RuleEngine`으로 쿼리 컴파일 + bad 예제 양성 / good 예제 음성을
확인했다 (검증 결과 절 참조). 채택 시 `conventions/typescript.json`에 항목만
추가하면 된다.

### A1. `ts-no-type-assertion` — `as` 단언 금지 (`as const` 제외) — **우선순위 1**

- **근거**: `ts-no-any`·`ts-no-non-null-assertion`과 같은 축의 구멍. `as`는
  컴파일러의 판단을 덮어쓰는 탈출구라 `any` 금지·`!` 금지를 해도 `as`로 다
  우회된다. 기존 룰셋의 "내로잉으로 증명하라" 철학을 완성하는 조각.
- **구현**: `forbid` / `((as_expression) @bad (#not-match? @bad "as\\s+const$"))`
  — `#not-match?` 술어로 `as const`만 예외 처리. `x as unknown as number` 같은
  이중 단언은 중첩 `as_expression` 2건으로 각각 잡힌다.
- **오탐 가드**: `as const`는 제외됨을 확인. `satisfies`는 별개 노드라 무관.
- bad: `const el = event.target as HTMLInputElement;`
- good: `if (el instanceof HTMLInputElement) { ... }` (내로잉으로 증명)

### A2. `ts-expect-error-needs-reason` — 사유 없는 `@ts-expect-error` 금지 — **우선순위 2**

- **근거**: `ts-no-ts-ignore`의 가이드라인이 "`@ts-expect-error`를 사유와 함께
  쓰라"고 말하지만 사유 없는 `@ts-expect-error`를 잡는 룰이 없다. 기존 룰의
  후속 구멍 봉쇄.
- **구현**: `forbid` / `((comment) @bad (#match? @bad "@ts-expect-error\\s*$"))`
  — 주석이 `@ts-expect-error`로 끝나면(= 뒤에 설명이 없으면) 위반.
- **오탐 가드**: 같은 줄에 사유가 붙으면 정규식이 매치되지 않음을 확인.
- bad: `// @ts-expect-error`
- good: `// @ts-expect-error TODO(#123): upstream types lag the runtime`

### A3. `ts-class-pascal-case` — 클래스는 PascalCase — **우선순위 3**

- **근거**: naming 계열의 명백한 공백. 함수(camelCase)·타입 별칭·인터페이스
  (PascalCase)는 있는데 클래스만 없다. 대칭성 회복.
- **구현**: `naming` / `(class_declaration name: (type_identifier) @name)` +
  regex `[A-Z][A-Za-z0-9]*`.
- bad: `class user_store {}` / good: `class UserStore {}`

### A4. `ts-no-nested-ternary` — 중첩 삼항 금지 — **우선순위 4**

- **근거**: 가독성 계열 첫 룰. 중첩 삼항은 분기 우선순위가 눈에 안 보여
  리뷰마다 재논쟁되는 고전 이슈.
- **구현**: `forbid` / 대체 패턴 리스트
  `[(ternary_expression consequence: (ternary_expression) @bad) (ternary_expression alternative: (ternary_expression) @bad)]`
  — consequence·alternative 어느 쪽 중첩이든 잡힘.
- **오탐 가드**: 단일 삼항은 허용 (음성 확인).
- bad: `const label = a ? "x" : b ? "y" : "z";`
- good: 이른 반환 또는 if/else.

### A5. `ts-prefer-template-literal` — 문자열 `+` 연결 금지 — **우선순위 5**

- **근거**: 문자열 리터럴이 낀 `+` 연결은 템플릿 리터럴로 항상 대체 가능하고
  가독성·타입 강제(암묵적 toString 지점 가시화) 면에서 우월.
- **구현**: `forbid` /
  `[(binary_expression left: (string) @bad operator: "+") (binary_expression operator: "+" right: (string) @bad)]`
  — **주의**: tree-sitter 쿼리는 필드를 문법 순서(left→operator→right)로 써야
  한다. `operator:`를 `left:`보다 앞에 쓰면 "Impossible pattern" 컴파일 오류
  (스모크에서 실제로 겪고 수정함 — 아래 결정 사항 참조).
- **오탐 가드**: 숫자 덧셈(`a + b`)은 string 노드가 없어 매치 안 됨 (음성 확인).
- bad: `const msg = "hello " + name + "!";` (2건 검출)
- good: `` const msg = `hello ${name}!`; ``

### A6. `ts-no-default-export` — default export 금지 — **우선순위 6**

- **근거**: named export는 이름이 단일 진실 공급원이 되어 rename 리팩터링이
  전체 추적되고 import 이름 표류가 없다. `as const` 파생 타입 철학(이름 있는
  단일 소스)과 결이 같다. 단, 프레임워크가 default export를 요구하는 파일
  (Next.js page 등)이 있어 **팀 채택 여부 확인이 필요한 opinionated 룰** —
  우선순위를 마지막에 둔 이유.
- **구현**: `forbid` / `(export_statement "default" @bad)`.
- bad: `export default function main() {}` / good: `export function main() {}`

## 제안 B — 분석기 룰 4종 (각각 태스크 1개 규모, 설계 스케치)

기존 분석기(optional-variant-bag ~ constant-callsite)와 같은 원칙을 따른다:
**파일 안에서 증거 사슬이 완결될 때만 발화** — 파일 밖 정보가 필요한 경우는
침묵(오탐 0 지향).

### B1. `ts-exhaustive-switch` (analyzer: `switch-exhaustiveness`) — **우선순위 1**

- **근거**: 파생 유니온 생태계(no-enum → as-const → constant-callsite)의 다음
  고리. 유니온으로 상태를 모델링해도 switch가 멤버를 빠뜨리면 이점이 소멸.
  멤버 추가 시 컴파일 타임에 모든 분기 누락을 잡는 게 유니온의 핵심 가치다.
- **검출**: 파일 안에 (1) `as const` 객체 + (2) 파생 별칭
  (`typeof Obj[keyof typeof Obj]`) + (3) 그 별칭으로 어노테이트된 값에 대한
  `switch`가 모두 보일 때, case 집합이 멤버 전체를 덮지 않고 `default`에서
  `never` 소진 체크(`const _exhaustive: never = value` 또는
  `assertNever(value)`)도 없으면 위반. constant-callsite의 증거 사슬 추적
  코드를 대부분 재사용 가능.
- **제안 메시지**: 누락 멤버를 나열 + never 소진 체크 패턴 제시.

### B2. `ts-prefer-literal-union-return` (analyzer: `stringly-literal-return`) — **우선순위 2**

- **근거**: `stringly-literal-param`의 거울상. 반환값이 닫힌 리터럴 집합인데
  반환 타입이 `string`이면 호출부에서 내로잉·완전성 검사가 불가능.
- **검출**: 반환 타입이 `string`(또는 미표기)인 함수의 모든 `return`이 문자열
  리터럴이고 서로 다른 값이 2개 이상이면 위반. param 분석기의 예외 규칙
  재사용: 리터럴 아닌 return이 하나라도 있으면 침묵.
- **제안**: as const 객체 + 파생 유니온 반환 타입 (기존 스타일과 동일 형태).

### B3. `ts-no-duplicate-object-shape` (analyzer: `duplicate-object-type`) — **우선순위 3**

- **근거**: `duplicate-literal-union`의 객체 버전. 같은 인라인 객체 타입 리터럴
  (`{ id: number; name: string }`)이 여러 자리에 반복되면 단일 진실 공급원이
  없어 필드 추가가 복사본 사냥이 된다.
- **검출**: 인라인 `object_type` 노드를 정규화(프로퍼티 정렬)해 핑거프린트,
  동일 형태가 2회 이상이면 위반. `fingerprint.py`의 AST 핑거프린트 인프라
  재사용 가능성이 높다. 별칭 선언 자체는 비발화(duplicate-literal-union과 동일
  규칙). 프로퍼티 1개짜리 형태는 우연 일치가 잦으므로 2개 이상만 발화.

### B4. `ts-prefer-derived-guard` (analyzer: `derived-guard`) — **우선순위 4**

- **근거**: `ts-pattern-const-object-enum`이 제시하는 가드 형태
  (`Object.values(Obj).includes`)가 있는데도 원시 리터럴 비교 체인
  (`v === 'a' || v === 'b'`)으로 손수 가드를 짜면 멤버 추가 시 가드가 조용히
  낡는다. constant-callsite의 "리터럴 대신 멤버 참조" 원칙의 가드 확장판.
- **검출**: 파일 안에 as const 객체 + 파생 별칭이 있고, 그 멤버 값들과 정확히
  같은 집합을 `===`/`!==` 비교 체인 또는 배열 리터럴 `.includes`로 검사하는
  함수/식이 있으면 위반. 집합이 부분 일치(일부 멤버만 검사)면 의도된 부분
  검사일 수 있으므로 침묵.

## 배제한 후보 (다음 세션이 같은 검토를 반복하지 않도록)

| 후보 | 배제 이유 |
|---|---|
| `prefer-nullish-coalescing` (`\|\|` → `??`) | 좌변 타입을 모르면 오탐 다발 (boolean·number에서 `\|\|`가 의도인 경우 흔함). 타입 체커 없이 결정적 판정 불가 |
| `no-magic-number` | 오탐/시끄러움 비율 최악의 고전 룰. 허용 리스트(0,1,-1,…) 관리 비용 대비 가치 낮음 |
| `prefer-readonly-param` / `readonly` 배열 강제 | 변이 여부는 함수 본문+호출부 전체 타입 정보가 필요. AST 단독으론 근거 부족 |
| `no-empty-interface` | tree-sitter 쿼리로 "자식 없음"을 표현하기 번거롭고 실익 미미. 필요 시 분석기로 가능하나 우선순위 없음 |

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `docs/tasks/TASK-0017-typescript-rule-proposal.md` | 생성 | 신규 TS 룰 10종 제안서 (이 문서) | 작성 완료 |
| `docs/tasks/INDEX.md` | 수정 | TASK-0017 등록, 마지막 번호 갱신, 백로그에 구현 후속 등록 | 갱신됨 |
| (scratchpad) `smoke_proposals.py` | 생성 | 제안 A 6종을 실제 RuleEngine으로 검증하는 스모크 스크립트 — 저장소 밖, 커밋 안 함 | 6/6 통과 |

## 결정 사항

- **결정**: 제안 A의 쿼리를 문서에 싣기 전에 전부 실제 엔진에서 실행 검증 /
  **이유**: tree-sitter 쿼리는 문법 지식만으로 쓰면 잘 틀린다 — 실제로
  `ts-prefer-template-literal` 초안이 필드 순서(`operator:`를 `left:`보다 앞에)
  때문에 "Impossible pattern" 컴파일 오류를 냈고, 문법 순서(left→operator→right)로
  고쳐서 통과시켰다. 검증 안 된 쿼리를 제안서에 실었으면 구현 세션이 그대로
  밟았을 함정.
- **결정**: `ts-no-type-assertion`에서 `as const`만 예외, `as unknown`은 예외로
  두지 않음 / **이유**: 이중 단언(`as unknown as T`)의 통로가 되므로. 정말
  필요한 지점은 `@ts-expect-error` + 사유(A2)로 명시적으로 뚫는 편이 추적 가능.
- **결정**: `ts-no-default-export`는 채택 보류 가능 표시(우선순위 최하) /
  **이유**: 프레임워크 관례(Next.js page 등)와 충돌 여지. 나머지 5종은 기존
  룰셋 철학의 직선 연장이라 충돌 없음.
- **결정**: 분석기 룰은 설계 스케치만 하고 구현하지 않음 / **이유**: 이 태스크의
  결과물은 제안. 각 분석기는 TASK-0008~0015 전례상 태스크 1개 규모.

## 검증 결과

```
$ python smoke_proposals.py        # scratchpad, RuleEngine 직접 호출
PASS ts-no-type-assertion:      bad=3 (expected 3), good=0
PASS ts-no-nested-ternary:      bad=1 (expected 1), good=0
PASS ts-no-default-export:      bad=1 (expected 1), good=0
PASS ts-class-pascal-case:      bad=1 (expected 1), good=0
PASS ts-prefer-template-literal: bad=2 (expected 2), good=0
PASS ts-expect-error-needs-reason: bad=1 (expected 1), good=0

6/6 candidates passed
```

기존 테스트는 건드린 코드가 없어 영향 없음 (이 태스크는 문서만 추가).

## 다음 단계 / 핸드오프

1. 채택할 룰을 정하면 제안 A는 태스크 1개로 묶어 `conventions/typescript.json`
   추가 + good/bad 예제 기반 테스트 작성 (쿼리는 이 문서 것을 그대로 사용 가능
   — 이미 엔진 검증됨).
2. 제안 B는 우선순위대로 태스크 1개씩 (B1 `switch-exhaustiveness`부터 권장 —
   기존 constant-callsite 증거 사슬 코드 재사용도가 가장 높다).
3. `ts-no-default-export`는 채택 전에 대상 코드베이스의 프레임워크 관례 확인.
