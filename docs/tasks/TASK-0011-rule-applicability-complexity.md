# TASK-0011: 룰 복잡도 스펙트럼 추천 — 마이닝 제거 + 지배 사이트 기반 적용성 신호

| | |
|---|---|
| **상태** | done |
| **브랜치** | claude/rule-recommendation-complexity-4fs7wh |
| **시작일** | 2026-07-13 |
| **완료일** | 2026-07-13 |
| **작업 세션** | 1 (TASK-0010 직후, 같은 세션) |

## 목표

TASK-0010에 대한 사용자 정정: "룰추천 복잡한거"는 **룰이 복잡한 정도**(단순
forbid/naming ↔ 복잡한 analysis 룰)를 추천이 전부 다루라는 뜻이지, 새 룰을
만들어 제안하라는 뜻이 아니었다.

이에 따라:

1. **네이밍 룰 마이닝 제거** — 요청 범위 밖 (proposals, `--no-mining`, `mine`
   파라미터 전부 삭제).
2. **복잡한 룰도 지배 사이트를 계수** — analysis 룰은 지금까지 "사이트 계수
   불가"(위반만 셈)로 뭉개졌다. 분석기마다 후보 모집단이 명확하므로
   (optional-variant-bag = 옵셔널 ≥2 인터페이스, record-key-inference =
   string 키 맵 어노테이션) 후보 카운터를 등록해 naming 룰과 같은 순응률
   기반 판정을 받게 한다.
3. **적용성(applicability) 검색 신호** — `search(code=...)` 시 룰 컨벤션의
   지배 사이트를 코드에서 세어 랭킹에 반영. 복잡한 룰은 BM25/핑거프린트로는
   안 잡히던 것(위반 전 단계의 구조적 전제조건)이 이제 코드 구조로 추천된다
   — 옵셔널 잔뜩인 인터페이스가 있으면 ts-no-optional-variant-bag이
   generate/review 프롬프트에 올라온다.

**DoD**: (a) proposals가 recommend 출력에서 사라짐, (b) analysis 룰이
recommend에서 sites/순응률 기반 4단 판정을 받음 (후보 0이면
insufficient_evidence — 전에는 근거 없이 adopt), (c) `store.search`에 룰
적용성 신호가 붙어 구조적 전제조건만으로 복잡한 룰이 상위 랭크됨,
(d) 룰 타입 전체(naming/require/require_in/forbid/analysis)의 사이트 시맨틱이
`governed_sites()` 한 곳에 정의됨, (e) 전체 pytest 통과.

## 진행 상황

- [x] `astcore/analyzers.py` — 분석기별 후보 사이트 카운터 (`ANALYZER_SITES`)
- [x] `astcore/rules.py` — `governed_sites()`: 룰 타입별 지배 사이트 시맨틱 단일화
- [x] `rag/recommend.py` — 마이닝 제거 + analysis 룰 사이트 계수
- [x] `rag/store.py` — 검색 적용성 신호 (`applies`, 룰 컨벤션 한정)
- [x] CLI/MCP 표면 정리 (`--no-mining`/`mine` 제거, 문서화 갱신)
- [x] 테스트 교체/추가 + 전체 pytest
- [x] INDEX/태스크 문서 갱신

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/analyzers.py` | 수정 | `optional_variant_bag_sites`/`record_key_inference_sites` 후보 카운터 + `ANALYZER_SITES` 레지스트리 — 위반 판정과 같은 순회 로직으로 후보 모집단만 계수 | 검증됨 |
| `src/acode/astcore/rules.py` | 수정 | `governed_sites(rule, root, language)` — naming=캡처 수, require_in=scope 수, require=1(파일), forbid=금지 구문 매치 수, analysis=ANALYZER_SITES 위임(카운터 없으면 None) | 검증됨 |
| `src/acode/rag/recommend.py` | 수정 | 마이닝 전면 제거(-180줄), analysis 브랜치가 후보 카운터로 sites를 채움 → 순응률 판정 경로 진입 | 검증됨 |
| `src/acode/rag/store.py` | 수정 | `_rule_applicability()` + search에 `applies` 신호(가중치 0.45, 룰 컨벤션 한정, 사이트 3개에서 포화) — 정규화를 컨벤션별 파트 합으로 변경 | 검증됨 |
| `src/acode/cli.py` | 수정 | `--no-mining` 제거 | 검증됨 |
| `src/acode/mcpserver/server.py` | 수정 | `recommend_rules`에서 `mine` 제거, `search_conventions` 독스트링에 적용성 신호 명시 | 검증됨 |
| `tests/test_recommend.py` | 재작성 | TestMining(9개) 삭제; TestGovernedSites(룰 타입 5종 사이트 시맨틱+분석기 카운터), TestComplexRuleEvidence(순응률 판정·후보 0=insufficient·record 후보 계수), TestApplicabilitySearch(위반 전 추천·무관 룰 0점·패턴 무영향), 단일 파일 루트 추가 — 29개 | 29 passed |

## 결정 사항

- **마이닝은 완전 삭제** (주석 처리/플래그 유지 아님): 사용자가 명시적으로
  범위 밖이라 했고, 죽은 기능을 남기면 다음 세션이 유지보수한다. 커밋
  히스토리에 남아 있으므로 필요해지면 TASK-0010 커밋에서 복원.
- **analysis 룰의 사이트 = 분석기의 후보 모집단**: 위반의 상위집합이 되도록
  카운터를 위반 판정과 같은 구조 조건으로 정의 (옵셔널 ≥2 인터페이스,
  string 키 맵 어노테이션 변수). 후보 0 + 위반 0 → 이제
  `insufficient_evidence` — 전에는 "위반 없음 = adopt"였는데 인터페이스가
  하나도 없는 코드베이스에서 adopt는 무근거였다.
- **forbid는 recommend에서 여전히 분산 판정**: "안 쓴 횟수"는 모집단이
  없어 순응률을 만들 수 없다. 단 **검색 적용성**에서는 forbid 매치 수를
  사이트로 쓴다 — 목적이 다르다 (채택 근거 vs 지금 이 코드에 걸리는가).
- **적용성 신호는 룰 컨벤션 한정, 패턴은 기존 AST 코사인 유지**: 패턴에는
  "지배 사이트" 개념이 없다. 정규화를 전역 가중치 합 → 컨벤션별 보유 파트
  합으로 바꿔 패턴이 불리해지지 않게 함 (패턴 점수는 기존과 동일).
- 포화 상수: 검색 적용성은 사이트 3개에서 1.0 (스니펫 단위 검색이므로 낮게),
  recommend confidence는 기존 20 유지 (코드베이스 단위).

## 검증 결과

- `pytest` 전체: **154 passed, 2 skipped**, 회귀 0 (test_recommend 23→29;
  기존 검색/파이프라인 테스트 전부 통과 — 패턴 랭킹 무영향 확인)
- 실전 스모크 1 — analysis 룰 순응률 판정: 후보 인터페이스 6개(위반 1)
  → `ts-no-optional-variant-bag` sites=6, violations=1, "5/6 sites conform
  (83%)" → `fix_first` (전: 위반 파일 분산으로만 판정)
- 실전 스모크 2 — 후보 0의 정직한 판정: 인터페이스 없는 코드베이스 →
  `insufficient_evidence` "only 0 governed site(s); need 5" (전: 무근거 adopt)
- 실전 스모크 3 — 적용성 검색 (시드 TS store, 옵셔널 인터페이스 2개 스니펫,
  키워드·위반 없음): 룰 랭킹 1·2위가 코드를 실제 지배하는 두 룰 —
  `ts-interface-pascal-case`(0.681), `ts-no-optional-variant-bag`(0.536,
  `rule_applicability=0.667`) — 나머지 룰은 applies 0으로 0.16대에 침전.
  `Record<string,V>` 스니펫 → `ts-no-wide-record-key` 1위(0.480, 2위의
  2.3배). 패턴 검색 결과는 신호 추가 전과 동일.
- recommend 출력에서 `proposals` 키 제거 확인, 동일 입력 2회 JSON 완전 일치

## 다음 단계 / 핸드오프

- MCP 서버 재연결 필요 (`/mcp` reconnect)
- 후속 아이디어(백로그): forbid 룰의 대체 패턴 존재 신호(enum 0 + as const
  존재 → adopt 강화), generate 경로에서 task 텍스트 기반 적용성(코드가 없어
  구조 신호를 못 쓰는 경로)
