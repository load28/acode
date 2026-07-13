# TASK-0008: 옵셔널 난발 검출 — analysis 룰 타입 + optional-variant-bag 분석기

| | |
|---|---|
| **상태** | done |
| **브랜치** | main |
| **시작일** | 2026-07-13 |
| **완료일** | 2026-07-13 |
| **작업 세션** | 1 |

## 목표

사용자 컨벤션: "옵셔널(`?`)은 정말 조건부로 들어오는 데이터에만 쓴다. 여러
변형(variant)을 하나의 인터페이스에 옵셔널로 퉁치지 말고, 판별 키 기반
discriminated union으로 분리해 어느 타이밍에 어떤 데이터가 필요한지 타입
추론이 되게 한다."

개수 임계값 같은 조잡한 기준이 아니라 **"이 인터페이스가 키 기반 유니언으로
분리될 수 있는가"를 기계적으로 추론**해야 한다. tree-sitter 쿼리는 집합
비교/클러스터링을 표현할 수 없으므로, 룰 엔진에 다섯 번째 룰 타입
`analysis`(내장 결정적 분석기, 순수 Python, LLM 없음)를 추가한다.

**DoD**: (a) 판별 키 신호와 사용처 클러스터 신호가 각각 검출되고, (b) 정당한
옵셔널(증거 없음)은 침묵하며, (c) tsx에서도 상속 동작하고, (d) 시드 컨벤션이
자가 검증을 통과하며, (e) 전체 pytest 통과.

## 진행 상황

- [x] 그래머 노드 형태 검증 (property_signature "?", union_type, satisfies/as)
- [x] `astcore/rules.py` — `analysis` 룰 타입 (Rule.analyzer 필드, validate, dispatch)
- [x] `astcore/analyzers.py` — `optional-variant-bag` 분석기 (신호 A/B)
- [x] `mcpserver/server.py` — add_convention에 analyzer 파라미터
- [x] `conventions/typescript.json` — analysis 룰 + discriminated union 패턴 시드
- [x] 테스트 + 전체 pytest
- [x] DB 재시드

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 생성 | 분석기 레지스트리(`ANALYZERS`) + `optional-variant-bag` 구현. 순수 AST 함수 — 인터페이스별 옵셔널 수집, 신호 A(리터럴 유니언 판별 키 공존), 신호 B(`: I`/`satisfies I`/`as I` 객체 리터럴의 옵셔널 키 집합이 서로소 클러스터 ≥2) | 검증됨 |
| `src/acode/astcore/rules.py` | 수정 | `RULE_TYPES`에 `analysis` 추가, `Rule.analyzer` 필드(+to/from_dict), `validate_rule`이 analyzer 존재 검증, `_check_rule`이 분석기로 dispatch (순환 import 회피 위해 lazy import) | 검증됨 |
| `src/acode/mcpserver/server.py` | 수정 | `add_convention`에 `analyzer` 파라미터, analysis는 query 대신 analyzer 필수 | 검증됨 |
| `conventions/typescript.json` | 수정 | `ts-no-optional-variant-bag`(analysis 룰) + `ts-pattern-discriminated-view-model`(패턴) 추가 — 기존 항목 포맷 보존, 27줄 append | 자가 검증 통과 |
| `tests/test_optional_variant_bag.py` | 생성 | 신호 A/B 발화, 겹치는 클러스터·증거 없음·옵셔널 1개는 침묵, tsx 다이얼렉트 동작, analyzer 검증 오류, 시드 자가 검증 | 10 passed |

## 결정 사항

- **판정 기준 (결정적, 증거 기반)** — 옵셔널 프로퍼티 ≥2인 인터페이스만 후보로 놓고:
  - **신호 A (선언)**: 같은 인터페이스에 string-literal union 타입 프로퍼티(판별 키
    후보)가 공존하면 위반 — 키 이름을 메시지에 지목.
  - **신호 B (사용처)**: 파일 내 그 인터페이스로 타입 지정된 객체 리터럴들
    (`: I`, `satisfies I`, `as I`)의 옵셔널 키 집합이 **서로소 클러스터 ≥2개**로
    갈리면 위반 — 클러스터 내용을 메시지에 포함. 집합이 겹치면(상관된 옵셔널)
    위반 아님.
  - 두 신호 다 없으면 침묵 — 옵셔널 자체는 합법.
- 개수 임계값 방식은 사용자가 명시적으로 거부 → 배제.

## 검증 결과

- `pytest` 전체: **113 passed, 2 skipped** (신규 10 포함), 회귀 0
- 실전 스모크 (tsx, 다이얼렉트 상속 경유): 판별 키 `state` + 옵셔널 3개인
  `PokemonCardView`를 정확히 지목 — "discriminant-candidate key 'state' already
  exists; split it into a discriminated union" (13개 룰 적용 중 유일 위반)
- 시드 2건 인서트 시 자가 검증 통과 (bad_example 검출 / good_example 통과)
- 라이브 DB 재시드 완료: `acode import conventions/typescript.json --replace` → 13건

## 다음 단계 / 핸드오프

- MCP 서버는 코드 변경(analysis 룰 타입) 반영을 위해 재연결 필요 (`/mcp` reconnect)
- 분석기 추가 방법: `astcore/analyzers.py`에 함수 작성 + `ANALYZERS` 등록 +
  `type: "analysis", analyzer: "<이름>"` 룰로 참조
- 한계(의도된 범위): 신호 B는 파일 내 사용처만 봄 (크로스 파일 타입 해석 없음),
  함수 리턴 타입 경유 리터럴은 미수집 — 필요해지면 후속 태스크로
