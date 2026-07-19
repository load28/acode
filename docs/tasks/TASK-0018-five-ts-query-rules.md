# TASK-0018: TS 쿼리 룰 5종 구현 (TASK-0017 제안 A1~A5)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/typescript-rule-proposal-dgz09f` |
| **시작일** | 2026-07-19 |
| **완료일** | 2026-07-19 |
| **작업 세션** | 1개 세션 (TASK-0017 제안 세션에서 연속) |

## 목표

TASK-0017 제안 A의 1~5번 룰을 `conventions/typescript.json`에 추가하고 테스트로
고정한다. 6번 `ts-no-default-export`는 프레임워크 관례 확인 전이라 **채택 보류**
(사용자 지시: "5번까지 진행").

Definition of Done:
- 5종 룰이 시드 파일에 들어가고 import 시 셀프 검증(bad 발화 / good 침묵) 통과
- 룰별 양성·음성 + 엣지 케이스 테스트, tsx 다이얼렉트 호환 테스트
- 전체 테스트 통과 유지

## 진행 상황

- [x] `ts-no-type-assertion` — `as` 단언 금지 (`as const` 예외, 이중 단언은 층별 검출)
- [x] `ts-expect-error-needs-reason` — 사유 없는 `@ts-expect-error` 금지
- [x] `ts-class-pascal-case` — 클래스 PascalCase (naming 계열 공백 보완)
- [x] `ts-no-nested-ternary` — 중첩 삼항 금지 (괄호 감싼 중첩 포함)
- [x] `ts-prefer-template-literal` — 문자열 `+` 연결 금지
- [x] tsx 트리에서 5종 모두 실행됨(skipped 아님) 확인
- [x] 전체 테스트 통과

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `conventions/typescript.json` | 수정 | 룰 5종 추가 (엔트리 23건이 됨). 쿼리는 TASK-0017에서 엔진 검증한 것을 사용하되, nested-ternary는 괄호 패턴 2건 보강 | import 셀프 검증 통과 |
| `tests/test_ts_query_rules.py` | 생성 | 시드 셀프검증 1 + 룰 5종 양성/음성/엣지 16 + tsx 다이얼렉트 2 = 19개 테스트 | 19 passed |
| `docs/tasks/TASK-0018-five-ts-query-rules.md` | 생성 | 이 문서 | — |
| `docs/tasks/INDEX.md` | 수정 | TASK-0018 등록, 백로그의 "제안 A 구현" 항목 갱신 | — |

## 결정 사항

- **결정**: `ts-no-nested-ternary` 쿼리에 `parenthesized_expression` 경유 패턴
  2건 추가 / **이유**: 테스트에서 `a ? (b ? "x" : "y") : "z"`가 직계 자식
  쿼리(`consequence: (ternary_expression)`)를 빠져나가는 걸 발견. tree-sitter
  쿼리엔 자손 결합자가 없어 괄호 1겹을 명시 패턴으로 커버했다. 괄호 2겹 이상
  (`((b ? x : y))`)은 못 잡지만 실코드에서 사실상 없는 형태라 수용.
- **결정**: `ts-no-default-export`(제안 A6)는 미구현 / **이유**: 사용자가 5번까지
  지시. opinionated 룰이라 대상 코드베이스의 프레임워크 관례(Next.js page 등)
  확인 후 별도 결정 (TASK-0017 문서 참조).
- **결정**: 이중 단언 `as unknown as number`는 위반 2건으로 보고됨을 테스트로
  고정 / **이유**: 층마다 하나의 override라는 룰 의미를 명시.
- **결정**: 새 룰의 카테고리로 `readability` 신설 (nested-ternary,
  template-literal) / **이유**: 기존 types/naming/logging/errors 어디에도 안
  맞음. 메타데이터 카테고리는 자유 문자열이라 스키마 변경 없음.

## 검증 결과

```
$ python -m pytest tests/ -q
217 passed, 3 skipped in 2.82s     # 기존 198 + 신규 19, 스킵 3은 기존과 동일(선택 설치 엔진)

$ python -m pytest tests/test_ts_query_rules.py -q
19 passed in 0.39s
```

- 시드 import 셀프 검증: `test_seed_file_self_verifies_on_import`에서 5종 모두
  added에 포함됨 확인 (bad_example 발화 + good_example 침묵을 store가 강제).
- tsx 호환: JSX 컴포넌트에서 `as` 단언·중첩 삼항이 검출되고, 5종 중 어느 것도
  `skipped_rules`에 들어가지 않음 확인.
- 기존 테스트 회귀 없음 (AS_CONST_CODE 등 전체 룰 통과 예제 영향 없음).

## 다음 단계 / 핸드오프

- TASK-0017 제안 B (분석기 4종)는 백로그에 유지 — `switch-exhaustiveness`부터
  착수 권장.
- `ts-no-default-export` 채택 여부는 사용자 결정 대기 (별도 태스크로 승격 가능).
