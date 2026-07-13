# TASK-0007: JSX/TSX 다이얼렉트 기계 검증 지원

| | |
|---|---|
| **상태** | done |
| **브랜치** | main |
| **시작일** | 2026-07-13 |
| **완료일** | 2026-07-13 |
| **작업 세션** | 1 |

## 목표

React 프로젝트(.tsx)를 acode로 리뷰했을 때 발견된 문제를 해소한다:

1. `check_code(language="typescript")`에 JSX가 포함된 코드를 넘기면 TS 그래머로
   파싱해 `syntax_ok: false`가 되고, 그 상태에서 forbid/naming 매치도 누락된다.
2. `language="tsx"`로 넘기면 파싱은 되지만 룰이 전부 `typescript` 언어에 묶여
   있어 적용 룰이 0개가 된다 (`RuleEngine.check`가 `rule.language != language`를
   스킵, 스토어 하드필터도 정확 일치만).
3. `ts-func-camel-case`가 React 컴포넌트(PascalCase 함수)를 위반으로 판정한다.
4. (사용자 결정) JSX 고유의 컨벤션을 담을 **tsx 전용 룰셋**을 신설한다 —
   상속만으로는 JSX 자체를 검사하는 룰이 없어 반쪽짜리이므로.

**DoD**: TSX 코드(예: PokemonCard 컴포넌트)를 `typescript` 또는 `tsx` 어느
언어로 넘겨도 (a) 문법 파싱이 성공하고, (b) `typescript` 룰(ts-no-enum 등)이
그대로 적용되며, (c) PascalCase 컴포넌트가 naming 위반으로 오탐되지 않고,
(d) tsx 전용 룰이 JSX 안티패턴을 검출하며, (e) 전체 pytest가 통과한다.

## 진행 상황

- [x] 문제 재현/원인 분석 (실제 React 프로젝트 리뷰에서 발견)
- [x] `astcore/parser.py` — 다이얼렉트 개념 (`rule_languages`, `resolve_dialect`)
- [x] `astcore/rules.py` — 검사 그래머로 쿼리 컴파일, 베이스 언어 룰 적용, `skipped_rules`
- [x] `rag/store.py` — 언어 하드필터를 다이얼렉트 포함(`IN`)으로 확장
- [x] `agent/steps.py` — override 필터 (`metadata.overrides`)
- [x] `agent/pipeline.py`, `agent/adk.py`, `mcpserver/server.py` — 검사 전 다이얼렉트 해석
- [x] `conventions/tsx.json` — tsx 전용 룰 4종 신설 (사용자가 후보에서 선택)
- [x] 테스트 작성 + 전체 pytest 통과

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/parser.py` | 수정 | `_DIALECT_BASE`/`_DIALECT_UPGRADE` 맵, `rule_languages()`(다이얼렉트→베이스 언어 룰 상속), `resolve_dialect()`(typescript로 온 JSX 코드를 tsx로 자동 승격 — 원 그래머 파싱 실패 + 다이얼렉트 성공일 때만) | 검증됨 |
| `src/acode/astcore/rules.py` | 수정 | `RuleEngine.check`가 다이얼렉트를 해석하고 베이스 언어 룰도 적용, 쿼리를 **검사 그래머**로 컴파일. 다이얼렉트 그래머에 없는 노드 타입을 쓰는 룰은 `skipped_rules`로 보고(침묵 스킵 금지). `CheckReport.skipped_rules` 추가 | 검증됨 |
| `src/acode/rag/store.py` | 수정 | `list(language=)`가 `rule_languages()` 기반 `IN` 필터 사용 — tsx 조회 시 typescript 컨벤션 포함 | 검증됨 |
| `src/acode/agent/steps.py` | 수정 | `applicable_rules`/`rules_from_hits`가 다이얼렉트 인지 + 다이얼렉트 룰의 `metadata.overrides`로 베이스 룰 대체 (`_drop_overridden`) | 검증됨 |
| `src/acode/agent/pipeline.py` | 수정 | review 경로에서 룰 조회 전에 `resolve_dialect`로 언어 해석 | 검증됨 |
| `src/acode/agent/adk.py` | 수정 | review 경로 동일 처리 | 검증됨 |
| `src/acode/mcpserver/server.py` | 수정 | `check_code`가 룰 조회 전에 `resolve_dialect` 적용 | 검증됨 |
| `conventions/tsx.json` | 생성 | tsx 전용 룰 4종: `tsx-func-component-naming`(camelCase\|PascalCase, `overrides: ts-func-camel-case`), `tsx-no-dangerously-set-inner-html`, `tsx-no-inline-style`, `tsx-no-react-fc` — 전부 good/bad example 자가 검증 통과 | 검증됨 |
| `tests/test_tsx_dialect.py` | 생성 | 다이얼렉트 해석(각도 assertion 비승격 포함)/룰 상속/override/skipped_rules/스토어 필터/tsx 룰 발화 | 14 passed |

## 결정 사항

- **tsx를 별도 언어가 아닌 typescript의 "다이얼렉트"로 모델링 + tsx 전용 룰셋 병행.**
  베이스 룰(no-enum 등)은 상속으로 tsx에서도 살리고, JSX 고유 컨벤션은
  `conventions/tsx.json`에 `language: "tsx"` 룰로 따로 둔다. tree-sitter의 tsx
  그래머는 TS 그래머와 노드 이름을 공유하는 상위집합(단 `<T>expr` type assertion
  제외)이라 TS용 쿼리가 그대로 컴파일된다. 배제한 대안: 룰 JSON을 tsx로 전부
  복제(이중 관리), LLM 변환 경유(결정성 훼손).
- **자동 승격은 "원 그래머 실패 + 다이얼렉트 성공"일 때만.** 순수 함수라 결정성
  유지. `<number>x` 같은 TS 전용 문법은 TS로 잘 파싱되므로 승격되지 않음(테스트로 고정).
- **다이얼렉트 그래머에서 컴파일 안 되는 쿼리는 `skipped_rules`로 명시 보고.**
  침묵 스킵은 "전부 검사했다"로 오독되므로 금지.
- **컴포넌트 naming 충돌은 override 메커니즘으로 해결.** 다이얼렉트 룰의
  `metadata.overrides`에 베이스 룰 id를 적으면 해당 다이얼렉트에서 베이스 룰을
  대체. tree-sitter 쿼리는 서브트리 부정("JSX를 반환하는 함수만 예외")을 표현할
  수 없어 tsx 전체에서 camelCase|PascalCase를 허용하는 완화 룰로 타협.
- **tsx 시드 룰 4종은 사용자가 후보 중 직접 선택** (naming 예외 /
  dangerouslySetInnerHTML 금지 / 인라인 style 금지 / React.FC 금지 — 전부 채택).
- `javascript`는 그래머가 JSX를 네이티브 지원하므로 다이얼렉트 불필요
  (`jsx` alias 기존 유지).

## 검증 결과

- `pytest` 전체: **103 passed, 2 skipped** (신규 14 포함), 회귀 0
- 실전 스모크: TASK 발단이 된 `PokemonCard.tsx`를 `language="typescript"`로 검사 →
  `language: "tsx"`로 자동 승격, `syntax_ok: true`, 적용 룰 12개
  (ts 8 상속 + tsx 4, `ts-func-camel-case`는 override로 제외), PascalCase 오탐 0,
  신규 룰이 실제 위반 1건(`tsx-no-inline-style`, line 25) 검출
- `conventions/tsx.json` 4종 모두 인서트 시 자가 검증(good 통과/bad 검출) 통과

## 다음 단계 / 핸드오프

- 새 다이얼렉트가 필요하면 `parser._DIALECT_BASE`/`_DIALECT_UPGRADE`에 등록만
  하면 됨 (메커니즘은 언어 무관)
- `generate_code`는 코드 입력이 없어 자동 승격 불가 — React 컴포넌트 생성 시
  호출자가 `language="tsx"`를 명시해야 tsx 룰이 적용됨
- 백로그 후보: tsx 룰 확충 (배열 index key 금지 등은 tree-sitter 쿼리 표현력
  한계로 보류)
