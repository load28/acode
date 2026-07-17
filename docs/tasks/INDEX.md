# 태스크 인덱스

> 이 저장소의 모든 작업 기록. 세션 시작 시 이 파일부터 읽는다.
> 규칙: [/CLAUDE.md](../../CLAUDE.md) · 새 태스크: [TEMPLATE.md](TEMPLATE.md) 복사
> 상태: `todo` 착수 전 · `in_progress` 진행 중 · `blocked` 대기(문서에 사유) · `done` 완료+검증

**마지막 태스크 번호: 0014** (새 태스크는 0015부터)

| ID | 제목 | 상태 | 시작 | 완료 | 주요 파일 |
|---|---|---|---|---|---|
| [TASK-0001](TASK-0001-initial-implementation.md) | AST 기반 컨벤션 코딩 에이전트 초기 구현 (ADK + MCP) | done | 2026-07-12 | 2026-07-12 | `src/acode/**`, `conventions/*.json`, `tests/**`, `pyproject.toml` |
| [TASK-0002](TASK-0002-task-tracking-system.md) | 태스크 단위 작업 기록 체계 도입 | done | 2026-07-12 | 2026-07-12 | `CLAUDE.md`, `docs/tasks/**`, `README.md` |
| [TASK-0003](TASK-0003-rag-corpus-search-engine.md) | 실제 RAG 코퍼스 구축 + 하이브리드 서치엔진 (BM25+AST+메타) | done | 2026-07-12 | 2026-07-12 | `src/acode/rag/textindex.py`, `src/acode/rag/corpus.py`, `src/acode/rag/store.py`, `conventions/*.json`, `src/acode/cli.py` |
| [TASK-0004](TASK-0004-oss-search-engines.md) | 검색 엔진을 선두 오픈소스로 교체 (Tantivy + FAISS, 빌트인 폴백) | done | 2026-07-12 | 2026-07-12 | `src/acode/rag/engines.py`, `src/acode/rag/store.py`, `pyproject.toml`, `tests/test_engines.py` |
| [TASK-0005](TASK-0005-metadata-wildcard-fix.md) | 메타데이터 필터 와일드카드 시맨틱 수정 (실동작 데모에서 발견) | done | 2026-07-12 | 2026-07-12 | `src/acode/rag/store.py`, `tests/test_rag.py` |
| [TASK-0006](TASK-0006-ts-ruleset-pipeline-trace.md) | TS 룰셋 확충 (enum→as const 등) + 파이프라인 트레이스 로깅 + E2E | done | 2026-07-12 | 2026-07-12 | `conventions/typescript.json`, `src/acode/agent/pipeline.py`, `src/acode/cli.py`, `tests/test_ts_conventions.py` |
| [TASK-0007](TASK-0007-tsx-dialect-support.md) | JSX/TSX 다이얼렉트 기계 검증 지원 (자동 승격 + 룰 상속 + override + tsx 룰셋) | done | 2026-07-13 | 2026-07-13 | `src/acode/astcore/parser.py`, `src/acode/astcore/rules.py`, `src/acode/agent/steps.py`, `conventions/tsx.json` |

| [TASK-0008](TASK-0008-optional-variant-bag-analyzer.md) | 옵셔널 난발 검출 — analysis 룰 타입 + optional-variant-bag 분석기 | done | 2026-07-13 | 2026-07-13 | `src/acode/astcore/analyzers.py`, `src/acode/astcore/rules.py`, `conventions/typescript.json`, `tests/test_optional_variant_bag.py` |

| [TASK-0009](TASK-0009-record-key-inference-analyzer.md) | Record 키 넓힘 검출 — record-key-inference 분석기 | done | 2026-07-13 | 2026-07-13 | `src/acode/astcore/analyzers.py`, `conventions/typescript.json`, `tests/test_record_key_inference.py` |

| [TASK-0010](TASK-0010-four-new-ts-analyzers.md) | TS 분석기 4종 (boolean-variant-bag, stringly-literal-param, duplicate-literal-union, as-const-candidate) | done | 2026-07-16 | 2026-07-16 | `src/acode/astcore/analyzers.py`, `conventions/typescript.json`, `tests/test_boolean_variant_bag.py`, `tests/test_stringly_literal_param.py`, `tests/test_duplicate_literal_union.py`, `tests/test_as_const_candidate.py` |

| [TASK-0011](TASK-0011-literal-union-param-as-const-style.md) | literal-union-param 제안을 as const 파생 타입 스타일로 | done | 2026-07-16 | 2026-07-16 | `src/acode/astcore/analyzers.py`, `conventions/typescript.json`, `tests/test_stringly_literal_param.py` |

| [TASK-0012](TASK-0012-duplicate-union-as-const-style.md) | duplicate-literal-union 제안도 as const 파생 타입 스타일로 | done | 2026-07-16 | 2026-07-16 | `src/acode/astcore/analyzers.py`, `conventions/typescript.json`, `tests/test_duplicate_literal_union.py` |

| [TASK-0013](TASK-0013-constant-callsite-analyzer.md) | constant-callsite 분석기 — 파생 타입 파라미터는 상수 멤버로 호출 | done | 2026-07-16 | 2026-07-16 | `src/acode/astcore/analyzers.py`, `conventions/typescript.json`, `tests/test_constant_callsite.py` |

| [TASK-0014](TASK-0014-constant-initializer-extension.md) | constant-callsite 확장 — 변수 초기화·파라미터 기본값도 상수로 | done | 2026-07-16 | 2026-07-16 | `src/acode/astcore/analyzers.py`, `conventions/typescript.json`, `tests/test_constant_callsite.py` |

## 열린 태스크 (todo / in_progress / blocked)

없음.

## 백로그 (아이디어 — 착수 시 태스크로 승격)

- ADK 신규 `Workflow`(그래프) API 마이그레이션 — SequentialAgent/LoopAgent deprecation 해소
- 언어/프레임워크별 시드 컨벤션 확충 (Go, Java, Rust, NestJS, Spring 등 — 문법 휠은 준비됨)
- 대규모 코퍼스(수만 건) 대비: BM25 증분 색인 또는 SQLite FTS5 전환
