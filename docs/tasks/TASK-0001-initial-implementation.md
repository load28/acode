# TASK-0001: AST 기반 컨벤션 코딩 에이전트 초기 구현 (ADK + MCP)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/coding-agent-ast-rag-mcp-vzqm5x` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | session_018XxtzruUVu3MpJqCTkMQN9 |
| **커밋** | 3d627a5 |

## 목표

ADK 기반 코딩 에이전트를 MCP로 노출한다. 컨벤션은 RAG에 **실행 가능한 AST
룰**로 저장되고, 룰 판정은 tree-sitter 엔진이 **기계적·결정적**으로 수행하며
LLM은 종합(합성)만 담당한다. 검색은 메타데이터 + AST 유사도 기반.
LLM은 로컬 Claude Code CLI 기본, 없으면 env로 지정한 임의 프로바이더.
전부 오픈소스 의존성.

**완료 기준**: 테스트 전부 통과 + MCP stdio 핸드셰이크 실증 + CLI 스모크 통과.

## 진행 상황

- [x] AST 코어: 파서, 결정적 핑거프린트, 룰 엔진 (forbid/require/require_in/naming)
- [x] RAG 저장소: SQLite + 메타데이터 필터 + AST 유사도 검색 + 코드베이스 인덱서
- [x] LLM 프로바이더: claude CLI(기본) / anthropic / openai호환 / litellm
- [x] ADK 에이전트: Sequential + Loop(검증→수리, 통과 시 escalate 종료) + ClaudeCodeLlm(BaseLlm)
- [x] MCP 서버: FastMCP stdio, 도구 9종
- [x] 시드 컨벤션 (python 5종, typescript 4종 — 저장 시 자가 검증 통과)
- [x] 테스트 47개, README, LICENSE(MIT), CLI

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `pyproject.toml` | 생성 | 패키지 정의, extras: adk/litellm/langs/dev, `acode` 엔트리포인트 | editable install 확인 |
| `src/acode/config.py` | 생성 | `ACODE_*` 환경변수 기반 설정 (DB 경로, LLM 프로바이더/모델/키/URL, 수리 횟수) | 테스트 통과 |
| `src/acode/astcore/parser.py` | 생성 | 언어별 문법 휠 레지스트리(py/js/ts/tsx/go/java/rust), 별칭·확장자 매핑 | 7개 언어 로드 확인 |
| `src/acode/astcore/fingerprint.py` | 생성 | 결정적 AST 핑거프린트: 노드타입 유니그램+부모>자식 바이그램 feature hashing 256차원, 식별자 제외 | 동일 입력=동일 벡터, 유사 구조 랭킹 테스트 통과 |
| `src/acode/astcore/rules.py` | 생성 | 결정적 룰 엔진: 4가지 룰 타입, tree-sitter 쿼리+프레디킷, 위치 포함 위반 리포트, 룰 검증 | 룰 타입별 테스트 통과, 출력 결정성 테스트 통과 |
| `src/acode/rag/store.py` | 생성 | SQLite 컨벤션 저장소. **룰 자가 검증**: bad_example을 못 잡거나 good_example을 잡으면 저장 거부. 검색 = 메타데이터 하드 필터 + (코드 있으면) 0.7*AST유사도+0.3*메타데이터, 동점은 id로 결정적 정렬 | 자가검증/필터/랭킹/결정성 테스트 통과 |
| `src/acode/rag/indexer.py` | 생성 | 코드베이스 인덱서: 최상위 함수/클래스 추출 → pattern 컨벤션으로 저장 (유저 코드 패턴 유사도 검색용) | 멱등성 포함 테스트 통과 |
| `src/acode/llm/base.py` | 생성 | `LlmProvider` ABC (system+prompt → text 단발 완성) | — |
| `src/acode/llm/claude_code.py` | 생성 | 로컬 `claude -p --output-format json --max-turns 1` 서브프로세스 프로바이더 | CLI 존재 확인, JSON 파싱 폴백 포함 |
| `src/acode/llm/http_providers.py` | 생성 | Anthropic API / OpenAI호환(/chat/completions, base_url로 Ollama·vLLM 등) / litellm(선택) | — |
| `src/acode/llm/factory.py` | 생성 | env 기반 프로바이더 해석: claude-code → anthropic → openai 자동 감지 순서 | — |
| `src/acode/agent/steps.py` | 생성 | 공유 결정적 단계: 검색, 룰 수집, 기계 검증, 프롬프트 빌드(기계 판정을 ground truth로 명시), 코드블록 추출 | — |
| `src/acode/agent/pipeline.py` | 생성 | 프레임워크 무관 파이프라인: 생성=검색→합성→검증→수리루프(제한), 리뷰=검색(AST유사도)→기계판정→LLM종합→수정본 재검증 | 수리루프/포기/거짓말탐지 테스트 통과 |
| `src/acode/agent/adk.py` | 생성 | ADK 레이어: 결정적 BaseAgent들 + SequentialAgent/LoopAgent 조립(검증 통과 시 escalate로 루프 종료), InMemoryRunner 실행기, `ProviderLlm`/`ClaudeCodeLlm` BaseLlm 어댑터 | ADK 경유 생성/리뷰/어댑터 테스트 통과 |
| `src/acode/mcpserver/server.py` | 생성 | FastMCP stdio 서버, 도구 9종 (search/check/generate/review/add/list/delete/index/server_info). ADK 백엔드 우선, 미설치 시 플레인 파이프라인 폴백 | 실제 stdio 핸드셰이크로 검증 |
| `src/acode/cli.py` | 생성 | `acode serve/import/export/list/check/search/index` | 전 명령 스모크 통과 |
| `conventions/python.json` | 생성 | py-no-print, py-snake-case-functions, py-no-mutable-default, py-docstring-required, py-pattern-fastapi-route | 저장 시 자가 검증 통과 |
| `conventions/typescript.json` | 생성 | ts-no-var, ts-no-console-log, ts-no-any, ts-interface-pascal-case | 저장 시 자가 검증 통과 |
| `tests/*` (5개 파일 + conftest) | 생성 | astcore/rag/pipeline/adk/mcp 47개 테스트, FakeProvider(스크립트된 LLM) | 47 passed |
| `README.md` | 수정 | 아키텍처, 설치, Claude Code 등록법, 컨벤션 작성법, LLM 설정 문서화 | — |
| `LICENSE`, `.gitignore` | 생성 | MIT | — |

## 결정 사항

- **tree-sitter-language-pack 배제** → 언어별 휠 패키지 사용.
  이유: language-pack 1.12.x는 런타임에 GitHub에서 문법을 다운로드(프록시
  환경에서 403, 오프라인/결정성 요구에 반함). 언어별 패키지는 휠에 문법 내장.
- **임베딩 모델 배제** → feature-hashing 구조 핑거프린트.
  이유: 검색까지 결정적·재현 가능해야 한다는 요구. 식별자/리터럴을 제외해
  "코드 모양" 유사도를 잡음.
- **LLM 도구 호출(function calling) 미사용** → 오케스트레이션을 코드/ADK
  워크플로 에이전트로 고정. 이유: LLM이 검증 단계를 건너뛸 수 없어야 함.
- **ADK 2.4의 신규 `Workflow`(그래프) API 대신 SequentialAgent/LoopAgent 유지.**
  신규 API는 아직 "LlmAgent sub-agent 불가" 상태의 실험 단계. deprecation
  경고는 나지만 안정적. → 후속 태스크 후보.
- **벡터 DB 배제** → SQLite 단일 파일 + 파이썬 코사인. 컨벤션 규모(수백~수천)에
  충분하고 외부 서비스 0.
- 라이선스 MIT.

## 검증 결과

- `pytest`: **47 passed** (ADK deprecation 경고 7건 외 클린)
- MCP stdio 실핸드셰이크: initialize → list_tools(9종) → check_code/server_info 호출 성공
- CLI 스모크: import(9건)/list/check(위반 4건 정확 검출)/search/index(9패턴) 전부 정상
- 시드 룰 9종 전부 저장 시 자가 검증 통과 (bad 검출 + good 통과)

## 다음 단계 / 핸드오프

- (선택) ADK 신규 `Workflow` API로 마이그레이션 — deprecation 경고 해소
- (선택) 언어/프레임워크별 시드 컨벤션 확충
- (선택) PR 생성은 사용자 요청 시
