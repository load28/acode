# 태스크 인덱스

> 이 저장소의 모든 작업 기록. 세션 시작 시 이 파일부터 읽는다.
> 규칙: [/CLAUDE.md](../../CLAUDE.md) · 새 태스크: [TEMPLATE.md](TEMPLATE.md) 복사
> 상태: `todo` 착수 전 · `in_progress` 진행 중 · `blocked` 대기(문서에 사유) · `done` 완료+검증

**마지막 태스크 번호: 0005** (새 태스크는 0006부터)

| ID | 제목 | 상태 | 시작 | 완료 | 주요 파일 |
|---|---|---|---|---|---|
| [TASK-0001](TASK-0001-initial-implementation.md) | AST 기반 컨벤션 코딩 에이전트 초기 구현 (ADK + MCP) | done | 2026-07-12 | 2026-07-12 | `src/acode/**`, `conventions/*.json`, `tests/**`, `pyproject.toml` |
| [TASK-0002](TASK-0002-task-tracking-system.md) | 태스크 단위 작업 기록 체계 도입 | done | 2026-07-12 | 2026-07-12 | `CLAUDE.md`, `docs/tasks/**`, `README.md` |
| [TASK-0003](TASK-0003-rag-corpus-search-engine.md) | 실제 RAG 코퍼스 구축 + 하이브리드 서치엔진 (BM25+AST+메타) | done | 2026-07-12 | 2026-07-12 | `src/acode/rag/textindex.py`, `src/acode/rag/corpus.py`, `src/acode/rag/store.py`, `conventions/*.json`, `src/acode/cli.py` |
| [TASK-0004](TASK-0004-oss-search-engines.md) | 검색 엔진을 선두 오픈소스로 교체 (Tantivy + FAISS, 빌트인 폴백) | done | 2026-07-12 | 2026-07-12 | `src/acode/rag/engines.py`, `src/acode/rag/store.py`, `pyproject.toml`, `tests/test_engines.py` |
| [TASK-0005](TASK-0005-metadata-wildcard-fix.md) | 메타데이터 필터 와일드카드 시맨틱 수정 (실동작 데모에서 발견) | done | 2026-07-12 | 2026-07-12 | `src/acode/rag/store.py`, `tests/test_rag.py` |

## 열린 태스크 (todo / in_progress / blocked)

없음.

## 백로그 (아이디어 — 착수 시 태스크로 승격)

- ADK 신규 `Workflow`(그래프) API 마이그레이션 — SequentialAgent/LoopAgent deprecation 해소
- 언어/프레임워크별 시드 컨벤션 확충 (Go, Java, Rust, NestJS, Spring 등 — 문법 휠은 준비됨)
- 대규모 코퍼스(수만 건) 대비: BM25 증분 색인 또는 SQLite FTS5 전환
