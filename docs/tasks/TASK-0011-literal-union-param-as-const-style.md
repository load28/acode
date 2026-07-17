# TASK-0011: ts-prefer-literal-union-param 제안 형태를 as const 파생 타입 스타일로 변경

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/optional-variant-vec-dl42zf |
| **시작일** | 2026-07-16 |
| **완료일** | 2026-07-16 |
| **작업 세션** | 1 |

## 목표

TASK-0010의 `stringly-literal-param` 분석기가 제안하는 수정 형태를
인라인/alias union(`type Align = 'left' | 'right'`)에서 **as const 객체 +
파생 타입** 스타일로 변경한다 (사용자 요청):

```typescript
const Align = {
  Left: 'left',
  Right: 'right',
} as const;

type Align = typeof Align[keyof typeof Align];
```

기존 `ts-no-enum` / `ts-pattern-const-object-enum` 컨벤션과 같은 형태라
룰셋 일관성이 좋아진다. 검출 로직은 변경 없음 — 위반 메시지의 제안 문구와
시드 컨벤션(guideline/message/good_example)만 바꾼다.

**DoD**: 위반 메시지가 파생 타입 스타일을 안내하고, 시드 자가 검증 포함
전체 pytest 통과.

## 진행 상황

- [x] `analyzers.py` — stringly_literal_param 위반 메시지 문구 변경
- [x] `conventions/typescript.json` — guideline/message/good_example 갱신
- [x] pytest + 문서/INDEX 갱신 + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | 위반 메시지 제안부를 "narrow it to that literal union (or a named alias)"에서 as const 객체 + `typeof Obj[keyof typeof Obj]` 파생 union 안내로 변경 | 검증됨 |
| `conventions/typescript.json` | 수정 | `ts-prefer-literal-union-param`의 guideline/message/good_example을 파생 타입 스타일로 갱신 (good_example: `const Align = {...} as const` + 동명 파생 타입 + `alignLabel(Align.Left)` 호출), 태그에 `as-const` 추가 | 자가 검증 통과 |
| `tests/test_stringly_literal_param.py` | 수정 | 메시지에 `as const` / `keyof typeof` 포함 단언 추가 (새 스타일 고정) | 통과 |

## 결정 사항

- 검출 조건은 그대로 두고 제안 문구만 변경 — 관찰된 리터럴 집합
  (`'left' | 'right'`)은 메시지에 계속 포함한다 (개발자가 객체 값을 바로
  채울 수 있도록).

- good_example의 동명 const/type(`Align`)은 값·타입 네임스페이스 분리로
  합법이며, 기존 `ts-pattern-const-object-enum`(LogLevel)과 같은 관례.

## 검증 결과

- `pytest` 전체: **166 passed, 3 skipped**, 회귀 0
- 시드 임포트: 19건 자가 검증 통과 (갱신된 good_example이 룰을 발화시키지
  않음 — 파라미터가 `Align` 타입이라 후보에서 제외됨을 확인)
- 스모크: 위반 메시지가 "hold the values in an `as const` object and type
  the parameter with the derived union (`type T = typeof Obj[keyof typeof
  Obj]`)"로 출력됨 (관찰된 리터럴 집합 `'left' | 'right'` 유지)

## 다음 단계 / 핸드오프

- 사용 환경에서 `acode import conventions/typescript.json --replace` 재실행
  필요 (TASK-0010과 동일).
