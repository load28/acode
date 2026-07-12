# TASK-0004: 검색 엔진을 선두 오픈소스로 교체 (Tantivy + FAISS)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/coding-agent-ast-rag-mcp-vzqm5x` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | session_018XxtzruUVu3MpJqCTkMQN9 |

## 목표

코퍼스 검색을 자체 구현 대신 해당 분야 선두 오픈소스 엔진으로 구동한다:

- **렉시컬(BM25)**: Tantivy — Rust 기반 Lucene 계승, 임베디드 풀텍스트 엔진 선두 (Quickwit 기반 엔진)
- **벡터(AST 핑거프린트)**: FAISS — Meta의 벡터 유사도 검색 업계 표준 (IndexFlatIP 정확 검색 = 결정성 유지)

엔진은 플러그블 백엔드로 추상화하고, 미설치 환경을 위해 기존 자체 구현을
폴백으로 유지한다. 검색 결과 랭킹의 결정성 불변식은 유지.

**완료 기준**: 두 엔진이 기본 경로로 동작, 폴백 자동 전환, 동일 질의에서
백엔드 간 동일한 상위 랭킹, 테스트 통과, 실코퍼스 시연. → 전부 충족.

## 진행 상황

- [x] `rag/engines.py` — LexicalEngine/VectorEngine 추상화 + Tantivy/FAISS/빌트인 4구현
- [x] `ConventionStore` 통합 (lazy build + 쓰기 시 무효화 유지)
- [x] env로 엔진 선택 (`ACODE_LEXICAL_ENGINE`, `ACODE_VECTOR_ENGINE` = auto|엔진|builtin)
- [x] pyproject `[search]` extra, corpus stats/server_info에 활성 엔진 표시
- [x] 백엔드 교차 테스트(랭킹 일치, 점수 일치) + 폴백 강제 테스트 — 총 88개
- [x] 실코퍼스 리빌드 + 시연, README 갱신

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/rag/engines.py` | 생성 | `LexicalEngine`(build/scores/stats) + `VectorEngine`(build/similarities) 추상화. 구현 4종: `TantivyLexicalEngine`(인메모리 인덱스, writer num_threads=1로 단일 세그먼트→결정적 BM25, 자체 토크나이저로 사전 토큰화해 camelCase 유지), `FaissVectorEngine`(IndexFlatIP 정확 코사인, float32), `BuiltinLexicalEngine`/`BuiltinVectorEngine`(기존 구현 랩). 팩토리는 auto시 선두 엔진 우선 | 백엔드별 6+3 테스트 통과 |
| `src/acode/rag/store.py` | 수정 | `text_index()` → `lexical_engine()`/`vector_engine()`. AST 유사도도 벡터 엔진 경유(기존 인라인 코사인 제거). 쓰기 시 두 엔진 모두 무효화 | 기존 하이브리드 테스트 전부 통과 |
| `src/acode/rag/corpus.py` | 수정 | `corpus_stats()`가 활성 엔진명 + 엔진별 통계를 `search` 키로 보고 | — |
| `src/acode/mcpserver/server.py` | 수정 | `server_info`에 `search_engines` 필드 | — |
| `pyproject.toml` | 수정 | `[search]` extra: tantivy>=0.24, faiss-cpu>=1.9, numpy>=1.26 | 설치 확인 (tantivy 0.26, faiss 1.14.3) |
| `tests/test_engines.py` | 생성 | 백엔드 파라미터라이즈 테스트(설치된 것만), **백엔드 간 합의 테스트**(렉시컬 top-1 일치, 벡터 점수 1e-5 일치), 팩토리/env 오버라이드/빈 코퍼스 | 26개 통과 |
| `tests/test_search_engine.py` | 수정 | corpus stats 키 변경 반영, 신규 파일(engines.py)이 인덱싱되며 랭킹이 바뀐 기대값 완화 | 통과 |
| `README.md` | 수정 | Tantivy/FAISS 링크와 함께 서치엔진 설명 갱신, `[search]` extra 추가 | — |

## 결정 사항

- **렉시컬 = Tantivy**: 임베디드 풀텍스트 엔진 중 선두 (Rust Lucene 계승,
  pip 휠 제공, 서버 불필요). Elasticsearch/OpenSearch는 서버 운영이 필요해
  "로컬 에이전트 도구" 성격과 맞지 않아 배제. Whoosh는 사실상 비유지보수.
- **벡터 = FAISS**: 벡터 유사도 검색의 업계 표준(Meta). `IndexFlatIP` +
  L2 정규화 핑거프린트 = **정확** 코사인 검색이라 결정성 불변식이 그대로 유지됨.
  ChromaDB/LanceDB/Qdrant도 검토 — 로컬 RAG 선두군이지만 임베딩 파이프라인
  중심 + 무거운 의존성 + HNSW 등 근사 검색은 재현성이 흔들릴 수 있어,
  "결정적 정확 검색"이 가능한 FAISS가 이 시스템에 적합.
- **빌트인 폴백 유지**: `[search]` extra 미설치 환경(제한된 플랫폼, 폐쇄망)에서도
  동작해야 함. 팩토리 auto가 자동 선택, env로 강제 가능. 백엔드 간 top-1 랭킹
  일치를 테스트로 보증.
- **Tantivy에 자체 토크나이저 텍스트를 주입**: tantivy 기본 토크나이저는
  camelCase를 분해하지 않으므로, 색인·질의 모두 acode 토크나이저로 사전
  토큰화해 공급 → 백엔드와 무관하게 동일한 매칭 시맨틱.
- **Tantivy writer를 단일 스레드로 고정**: 멀티스레드 쓰기는 세그먼트 분할이
  비결정적일 수 있어 num_threads=1로 단일 세그먼트 보장 → BM25 통계 결정적.
- textindex.py의 BM25Index는 빌트인 폴백 구현체로 존치.

## 검증 결과

- `pytest`: **88 passed** (기존 62 + 신규 26)
- 실코퍼스 리빌드: 99 엔트리, `corpus stats` → `lexical_engine: tantivy`,
  `vector_engine: faiss`
- 실질의: `--query "logging print forbidden"` → py-no-print 1위 (빌트인과 동일 랭킹),
  route 코드 AST 검색 → py-pattern-fastapi-route 1위 (ast=0.826, FAISS 경유)
- 결정성: tantivy+faiss 조합 동일 질의 2회 → 바이트 단위 동일 출력
- 폴백: `ACODE_*_ENGINE=builtin` 강제 시에도 동일 top-1
- 백엔드 합의: 벡터 점수 builtin vs faiss 1e-5 이내 일치 (float32 오차만)

## 다음 단계 / 핸드오프

- 코퍼스가 커지면 Tantivy 디스크 인덱스(현재 인메모리, 질의 시 lazy 빌드)로
  전환해 재빌드 비용 절감 가능
- FAISS도 수십만 엔트리 규모부터는 IndexIVF 등으로 전환 검토 (근사 검색 도입 시
  결정성 트레이드오프 문서화 필요)
