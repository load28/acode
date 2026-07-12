# TASK-0003: 실제 RAG 코퍼스 구축 + 하이브리드 서치엔진

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/coding-agent-ast-rag-mcp-vzqm5x` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | session_018XxtzruUVu3MpJqCTkMQN9 |

## 목표

지금 데이터를 기반으로 **실제로 돌아가는 RAG**를 만든다:

1. **코퍼스**: 시드 컨벤션 확장(python/typescript/javascript) + 이 레포 자체
   소스코드 인덱싱으로 실제 코퍼스를 구축하는 `acode corpus build` 파이프라인.
   코퍼스 원본(JSON)은 git 관리, 빌드된 DB는 아티팩트(재생성 가능).
2. **서치엔진**: 텍스트 질의를 위한 **BM25 역색인**(결정적, 외부 의존성 0)을
   추가해 기존 메타데이터 필터 + AST 핑거프린트와 결합한 하이브리드 랭킹 완성.
3. 실제 코퍼스를 빌드하고 실제 질의로 동작을 시연/검증.

**완료 기준**: corpus build 실행으로 실DB 생성, 텍스트/AST/메타데이터 질의가
실제 결과를 반환, 테스트 통과, 코퍼스가 나중에 업데이트 가능한 구조. → 전부 충족.

## 진행 상황

- [x] BM25 역색인 (`rag/textindex.py`) — 토크나이저(camelCase/snake_case 분해) + 결정적 랭킹
- [x] `ConventionStore.search`에 `query` 텍스트 파라미터 통합 (하이브리드 스코어)
- [x] MCP `search_conventions` / CLI `search`에 query 노출
- [x] 코퍼스 확장: python +3룰 +2패턴, typescript +2룰 +1패턴, javascript 신규(3룰+1패턴)
- [x] `acode corpus build/stats` 명령 (`rag/corpus.py`)
- [x] 실코퍼스 빌드 + 실질의 시연 (아래 검증 결과)
- [x] 테스트 15개 추가 (총 62개) + 문서 갱신

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/rag/textindex.py` | 생성 | BM25 역색인: 토크나이저(비영숫자 분리 + camelCase 분해 + 소문자화), postings/idf/score, [0,1] 정규화. k1=1.5, b=0.75 | 관련도·결정성·무매치 테스트 통과 |
| `src/acode/rag/store.py` | 수정 | `search()`에 `query` 파라미터 추가. 하이브리드 가중치: AST 0.45 + BM25 0.35 + 메타 0.20 (존재하는 신호로 재정규화). 순수 텍스트 검색은 무매치 엔트리 제외. add/delete 시 색인 무효화(lazy rebuild) | 하이브리드/무효화 테스트 통과 |
| `src/acode/rag/corpus.py` | 생성 | `build_corpus()`: conventions 디렉토리 일괄 로드(깨진 파일은 에러 보고만, 빌드 계속) + 소스 경로 인덱싱 + 빌드 리포트. `corpus_stats()`: 언어/종류별 구성 + BM25 통계 | 빌드/재빌드/에러 격리 테스트 통과 |
| `src/acode/cli.py` | 수정 | `acode corpus build/stats` 서브커맨드, `search --query` | 실행 시연 완료 |
| `src/acode/mcpserver/server.py` | 수정 | `search_conventions`에 `query` 파라미터 | MCP 경유 텍스트 검색 테스트 통과 |
| `conventions/python.json` | 수정 | +py-no-eval(#any-of?), +py-class-pascal-case, +py-no-bare-except(anchor 쿼리), +pytest/dataclass 패턴 2종 | 저장 시 자가 검증 통과 |
| `conventions/typescript.json` | 수정 | +ts-no-ts-ignore(주석 #match?), +ts-func-camel-case, +Result 유니온 패턴 | 자가 검증 통과 |
| `conventions/javascript.json` | 생성 | js-no-var, js-strict-equality(익명 연산자 토큰 쿼리 `["==" "!="]`), js-no-console, express 라우트 패턴 | 자가 검증 통과 |
| `tests/test_search_engine.py` | 생성 | 토크나이저/BM25/하이브리드/코퍼스 빌드/MCP 질의 15개 테스트 | 전부 통과 |
| `tests/test_mcp_server.py` | 수정 | 테스트용 룰 id를 py-no-eval → py-no-compile로 변경 (시드와 충돌) | 통과 |
| `README.md` | 수정 | Quick start를 corpus build 중심으로 재작성, 하이브리드 서치엔진 설명 | — |

## 결정 사항

- **BM25 직접 구현 (외부 검색 라이브러리 배제)**: rank-bm25/whoosh 등 대신
  ~120줄 직접 구현. 이유: 의존성 0 유지, 스코어 산식이 완전히 감사 가능해야
  "검색도 결정적"이라는 시스템 불변식이 지켜짐. 코퍼스 규모(수백~수천)에 충분.
- **색인은 lazy 재구축**: add/delete 시 무효화 플래그만 세우고 다음 질의에서
  전체 재구축. 코퍼스가 작아 증분 색인의 복잡도가 이득보다 큼. 코퍼스가 수만
  건이 되면 증분 갱신으로 전환 (핸드오프 참조).
- **가중치 0.45 AST / 0.35 BM25 / 0.20 메타**: 코드 형태 신호를 최우선(이
  시스템의 차별점), 텍스트는 발견용, 메타데이터는 이미 하드 필터로 걸러서 소프트
  점수 비중은 낮게. 존재하는 신호로 재정규화하므로 단일 신호 검색도 자연스러움.
- **순수 텍스트 검색은 무매치 제외**: 검색엔진 시맨틱(질의와 무관한 문서를
  반환하지 않음). 단, code 신호가 함께 있으면 AST 유사도가 캐리하도록 유지.
- **DB는 빌드 아티팩트, JSON이 소스 오브 트루스**: 바이너리를 git에 넣지 않고
  `acode corpus build` 재실행으로 언제든 재생성/업데이트. `--keep`으로 증분도 가능.
- **깨진 컨벤션 파일은 빌드를 중단시키지 않음**: 에러 리포트에 담고 나머지는
  로드 (코퍼스 업데이트 시 부분 실패 격리).

## 검증 결과

- `pytest`: **62 passed** (기존 47 + 신규 15)
- 실코퍼스 빌드: `acode corpus build --index src/acode` →
  **91 엔트리** (컨벤션 21 + 레포 소스 패턴 70), 룰 16, BM25 용어 860, 에러 0
- 실질의 시연 (빌드된 코퍼스 대상):
  - `--query "logging print forbidden"` → `py-no-print` 1위 (bm25=1.000)
  - `--query "bm25 inverted index tokenize"` → 방금 작성한 `BM25Index` 클래스
    패턴이 1위 — **레포 코드가 실제로 코퍼스에 들어가 검색됨**
  - route 형태 코드로 AST 검색 → `py-pattern-fastapi-route` 1위 (ast=0.826)
  - 텍스트+AST+메타 하이브리드 → 세 신호 결합 스코어 0.922로 정답 1위
  - 동일 질의 2회 → 출력 바이트 단위 동일 (결정성)

## 다음 단계 / 핸드오프

- 코퍼스 업데이트 절차: `conventions/*.json` 수정 또는 추가 → `acode corpus
  build --index <소스경로>` 재실행이 전부. MCP `add_convention`/`index_codebase`로
  런타임 증분 추가도 가능.
- (백로그) 코퍼스가 수만 건 규모가 되면: BM25 증분 색인, SQLite FTS5 검토
- (백로그) 언어별 코퍼스 확충 (go/java/rust는 문법 휠 준비됨, 룰만 추가하면 됨)
