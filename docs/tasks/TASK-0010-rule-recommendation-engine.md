# TASK-0010: 룰 추천 엔진 — 코드베이스 증거 기반 채택 판정 + 네이밍 룰 마이닝

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/rule-recommendation-complexity-4fs7wh |
| **시작일** | 2026-07-13 |
| **완료일** | 2026-07-13 |
| **작업 세션** | 1 |

## 목표

사용자 요청: "룰추천 복잡한거" — 위반 개수만 세는 단순 버전이 아니라,
**다중 신호(사이트 수·순응률·파일 분산·배타 증거)** 를 조합해 결정적으로
판정하는 복잡한 룰 추천.

코드베이스를 스캔해 두 가지를 추천한다:

1. **카탈로그 판정**: 저장소에 있는 모든 rule 컨벤션을 코드베이스 전체에
   돌려서, 룰 타입별로 "지배 사이트 수(opportunities)"와 순응률을 측정하고
   `adopt` / `fix_first` / `conflicts` / `insufficient_evidence` 4단 판정 +
   신뢰도(confidence)를 낸다. LLM 없음 — 같은 입력이면 항상 같은 출력.
2. **네이밍 룰 마이닝**: 카탈로그에 없는 네이밍 컨벤션을 코드베이스에서
   직접 캐낸다. 구성 단위(함수/클래스/인터페이스/타입별칭)별 식별자를 수집해
   스타일별 순응률을 재고, 지배 스타일이 임계치를 넘고 **배타 증거**(그
   스타일에만 맞는 샘플)가 최다일 때만 즉시 `add_convention` 가능한(자가 검증
   통과) 룰 제안을 만든다.

**DoD**: (a) 룰 타입별(naming/require_in/require는 사이트 계수, forbid/analysis는
파일 분산) 증거 수집이 결정적으로 동작, (b) 4단 판정 + confidence가 문서화된
공식대로 산출, (c) 마이닝 제안이 store.add 자가 검증을 통과하는 형태로 반환,
(d) 카탈로그에 이미 있는 네이밍 룰은 마이닝에서 중복 제거, (e) tsx가
typescript 룰을 상속해 집계, (f) CLI `acode recommend` + MCP `recommend_rules`
노출, (g) 전체 pytest 통과.

## 진행 상황

- [x] 추천 엔진 `src/acode/rag/recommend.py` (증거 수집 + 판정 + 마이닝)
- [x] CLI `acode recommend <path>` 서브커맨드
- [x] MCP 툴 `recommend_rules`
- [x] 테스트 `tests/test_recommend.py` + MCP 툴 노출 검증
- [x] 전체 pytest + 실전 스모크 (이 저장소 자체 스캔)
- [x] INDEX/태스크 문서 갱신

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/rag/recommend.py` | 생성 | 추천 엔진: 룰 타입별 사이트 계수(`_count_sites`), 파일 단위 집계(`_RuleStats`), 4단 판정+confidence(`_verdict`), 네이밍 마이닝(`_mine_naming`, 스타일 순응률+배타 증거 판별), 최상위 `recommend_rules()` | 23 테스트 통과 |
| `src/acode/cli.py` | 수정 | `acode recommend <path> [--language] [--max-files] [--min-sites] [--no-mining]` 서브커맨드 — JSON 출력 | 스모크 통과 |
| `src/acode/mcpserver/server.py` | 수정 | `recommend_rules` MCP 툴 — 제안은 `add_convention` 인자로 바로 넘길 수 있는 형태 | 툴 노출 테스트 통과 |
| `tests/test_recommend.py` | 생성 | 판정 4종·룰 타입별 계수·tsx 상속·마이닝(제안/배타증거 부재시 침묵/중복 제거/자가 삽입)·결정성(2회 실행 동일)·CLI/MCP 스모크 | 23 passed |

## 결정 사항

- **opportunities(지배 사이트 수)는 룰 타입별로 다르게 계수한다**:
  naming = 캡처된 식별자 수, require_in = scope 매치 수, require = 파일 수,
  forbid/analysis = 계수 불가(None) — 금지 룰은 "안 쓴 횟수"를 셀 수 없으므로
  파일 분산(violating_files/checked_files)으로 대체 판정한다. 단일 공식으로
  뭉개면 forbid 룰이 위반 0일 때 증거 없음과 구분이 안 되기 때문.
- **판정 임계치**: 순응률 ≥0.9 → `adopt`, ≥0.5 → `fix_first`, 미만 →
  `conflicts`(코드베이스가 반대 관행 — 채택하면 싸움). 사이트 < min_sites(5)
  → `insufficient_evidence`. forbid/analysis는 위반 0 → `adopt`, 위반 파일
  비율 ≤0.2 → `fix_first`, 초과 → `conflicts`.
- **confidence = base × saturation**: base는 순응률(계수 가능) 또는
  1−위반파일비율(불가), saturation은 min(1, 증거 수/20) — 증거 3개짜리
  100% 순응이 증거 40개짜리 95%보다 높게 나오는 역전을 막는다.
- **마이닝은 배타 증거를 요구한다**: 소문자 한 단어(`fetch`)는 camelCase와
  snake_case에 동시에 매치되므로 순응률만으로는 스타일을 못 가른다. 지배
  스타일은 "그 스타일에만 매치되는 샘플" 수가 단독 최다일 때만 제안 —
  전부 모호하면 침묵. 이것이 오탐(파이썬 코드에 camelCase 제안)을 막는
  결정적 장치.
- **마이닝 제안은 반환 전에 엔진으로 자가 검증한다**: good_example(실제
  코드베이스의 지배 샘플) / bad_example(regex에 안 걸리는 합성 이름)을
  만들어 store와 같은 방식으로 검증 — 반환된 제안은 그대로
  `add_convention` 하면 반드시 들어간다.
- **마이닝 대상 언어는 python/javascript/typescript(+tsx→ts 승격)로 한정**:
  go/java/rust는 그래머 노드명 미검증 상태로 넣으면 조용한 오동작 위험 —
  카탈로그 판정은 전 언어 동작하므로 마이닝만 후속 확장.
- LLM 후보 생성(위반 클러스터를 LLM에 보여 신규 룰 초안 생성)은 배제 —
  이 저장소의 원칙(추천은 재현 가능해야)과 충돌. 백로그에 아이디어로만 남김.

## 검증 결과

- `pytest` 전체: **148 passed, 2 skipped** (신규 23 포함), 회귀 0
- 실전 스모크 (이 저장소 `src/acode`를 python 카탈로그로 스캔, 26파일):
  - `py-no-eval`/`py-no-mutable-default`/`py-no-bare-except` → `adopt` (위반 0, confidence 1.0)
  - `py-snake-case-functions` → `adopt` (사이트 164, 순응률 100%, confidence 1.0)
  - `py-class-pascal-case` → `adopt` (35/38 순응 92% — `_RuleStats` 등 언더스코어 3건 목록화)
  - `py-no-print` → `fix_first` (print 12건이 26파일 중 1파일(cli.py)에 국한 — contained)
  - `py-docstring-required` → `conflicts` (164 scope 중 51 순응 31% — 실제로 이 저장소는 내부 함수에 docstring을 안 씀; 판정이 현실과 일치)
  - 마이닝: 빈 store로 스캔 시 `mined-python-function-naming`(snake_case,
    배타 증거 120/164, confidence 1.0) + `mined-python-class-naming`(PascalCase)
    제안 — 제안 dict 그대로 `store.add` 성공 (자가 검증 통과)
  - 시드 store로 스캔 시 proposals `[]` — 카탈로그 중복 제거 동작 확인
- 결정성: 동일 입력 2회 실행 JSON 완전 일치

## 다음 단계 / 핸드오프

- MCP 서버 재연결 필요 (`/mcp` reconnect) — 신규 툴 반영
- 후속(백로그): 마이닝 대상 go/java/rust 확장 (노드명 검증 후),
  forbid 룰의 "대체 패턴 존재" 신호 (예: enum 0 + as const 존재 → adopt 강화),
  크로스 파일 증거 (호출부 관행)
