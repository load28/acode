# TASK-0020: 강타입 핵심 쿼리 룰 3종 구현 (TASK-0019 제안 A 축소 채택)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/typescript-strict-type-rules-hcq34x` |
| **시작일** | 2026-07-19 |
| **완료일** | 2026-07-19 |
| **작업 세션** | 1개 세션 |

## 목표

TASK-0019 제안 A 6종 중 사용자가 채택한 **핵심 3종**만 구현한다. 선정 기준:
새 제약 추가가 아니라 **기존 룰(`ts-no-any`·`ts-no-type-assertion`)의 우회로
봉쇄** — 이게 없으면 기존 강타입 룰이 실질적으로 뚫려 있다.

1. `ts-no-angle-bracket-assertion` — `<T>expr` 옛 문법 단언 금지
2. `ts-no-wrapper-object-types` — `String`/`Number`/`Object`/`Function` 등 금지
3. `ts-no-implicit-any-param` — 함수 선언·메서드의 무어노테이션 파라미터 금지

나머지 3종(`explicit-export-return-type`·`no-empty-object-type`·`no-export-let`)은
**미채택** (소음/빈도 사유 — TASK-0019 문서 참조), 백로그에서도 제거한다.

Definition of Done:
- `conventions/typescript.json`에 3종 추가 (쿼리는 TASK-0019에서 엔진 검증된 것)
- good/bad 예제 기반 테스트 작성 (TASK-0018 `test_ts_query_rules.py` 형식)
- tsx 다이얼렉트 동작 확인 포함, 전체 테스트 통과

## 진행 상황

- [x] TASK-0019 검증 쿼리 재사용 확인 + tsx 다이얼렉트 사전 실측
- [x] `conventions/typescript.json`에 룰 3종 추가
- [x] `tests/test_strict_type_rules.py` 작성
- [x] 전체 테스트 통과 확인

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `conventions/typescript.json` | 수정 | 룰 3종 추가 (no-angle-bracket-assertion, no-wrapper-object-types, no-implicit-any-param) — 쿼리는 TASK-0019 검증본 그대로 | import 자가 검증 통과 |
| `tests/test_strict_type_rules.py` | 생성 | 3종 각각 양성/음성 + tsx 다이얼렉트 테스트 17건 | 17 passed |
| `docs/tasks/TASK-0020-three-strict-type-rules.md` | 생성 | 이 문서 | — |
| `docs/tasks/INDEX.md` | 수정 | TASK-0020 등록, TASK-0019 백로그 항목 정리 (채택분 구현됨·미채택분 제거 기록) | 갱신됨 |

## 결정 사항

- **결정**: TASK-0019 제안 A 중 3종만 구현, 나머지 3종은 백로그에 남기지 않고
  미채택으로 종결 / **이유**: 사용자 결정 — "중요한 것만". 선정 기준은 기존 룰
  우회로 봉쇄 여부. `explicit-export-return-type`은 기존 코드 발화량(소음),
  `no-empty-object-type`·`no-export-let`은 낮은 발생 빈도로 제외. 재론 시
  TASK-0019 문서에 검증된 쿼리가 그대로 남아 있다.
- **결정**: `ts-no-angle-bracket-assertion`은 tsx에서 skip되는 것을 정상 동작으로
  수용 (테스트로 고정) / **이유**: tsx 문법에는 `type_assertion` 노드 자체가
  없다 — JSX와 충돌해 그 문법을 쓸 수 없으므로 tsx에서 이 룰이 잡을 대상도
  존재하지 않는다. 엔진의 skipped_rules 처리로 안전하게 넘어감을 실측 확인.
- **결정**: `<const>expr`도 예외 없이 금지 (TASK-0019 결정 승계) / **이유**:
  const 단언의 저장소 표준 철자는 `as const` 하나로 통일.
- **결정**: `ts-no-implicit-any-param`에서 화살표 함수 파라미터 제외
  (TASK-0019 결정 승계) / **이유**: 콜백 위치의 화살표는 문맥 타입이 정확히
  추론 — 강제하면 이미 강타입인 코드에 소음.

## 검증 결과

```
$ python -m pytest -q
239 passed, 2 skipped
```

신규 테스트만:

```
$ python -m pytest tests/test_strict_type_rules.py -q
17 passed
```

## 다음 단계 / 핸드오프

- TASK-0019의 미구현 제안(분석기 B 2종, 패턴 C 2종)은 백로그 유지.
- 미채택 3종을 재론하려면 TASK-0019 문서의 검증된 쿼리를 그대로 사용 가능.
