# TASK-0006: TypeScript 룰셋 확충 (enum→as const 등) + 파이프라인 트레이스 로깅

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/typescript-ruleset-corpus-test-vkqu3s` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | session_01Bq6KgqJLAPp9z1X4tMmXGP |

## 목표

1. **룰셋 확충**: TypeScript 컨벤션에 새 룰을 추가해 코퍼스를 더 풍부하게 만든다.
   핵심 요구사항: **enum 금지 — 상수는 `as const` 객체로 만들고 그 객체에서
   타입을 유도**(`type T = typeof Obj[keyof typeof Obj]`)하는 룰 + 패턴.
   추가로 non-null assertion 금지, 타입 별칭 PascalCase 룰.
2. **파이프라인 트레이스 로깅**: generate/review 파이프라인의 각 단계
   (retrieve → synthesize → verify → repair 루프)를 구조화된 트레이스로 남겨서,
   **코드가 어떻게 생성되고 위반이 어떻게 수리되는지** 로그로 관찰 가능하게 한다.
3. **CLI에 generate/review 노출**: `acode generate` / `acode review --verbose`.
4. **코퍼스 재빌드 + E2E 테스트**: 실제 LLM(claude CLI)으로 enum이 나올 법한
   태스크를 돌려 로그로 개선 과정을 시연.

**완료 기준**: 새 룰 자가 검증 + 단위 테스트 통과, 트레이스가 결과 JSON과
로그에 남음, 코퍼스 빌드 반영, 실 LLM E2E 로그 확보. → 전부 충족.

## 진행 상황

- [x] tree-sitter 쿼리 사전 검증 (4종 모두 bad 플래그 + good 통과, `const enum`도 `enum_declaration`으로 잡힘)
- [x] `conventions/typescript.json`에 룰 3종 + 패턴 1종 추가 (자가 검증 통과, 총 11 엔트리)
- [x] 파이프라인 트레이스 로깅 (generate/review, `acode.pipeline` 로거 + 결과 `trace` 배열)
- [x] `acode generate` / `acode review` CLI + `--verbose` / `--no-trace`
- [x] 단위 테스트 10개 추가 (룰 6 + 생성 트레이스 2 + 리뷰 트레이스 1 + 검색 1)
- [x] 코퍼스 재빌드 + 실 LLM E2E 시연 (아래 검증 결과에 로그)
- [x] 문서/INDEX 갱신 + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `conventions/typescript.json` | 수정 | +`ts-no-enum`(forbid `(enum_declaration)`, const enum 포함), +`ts-pattern-const-object-enum`(as const 객체 + 유도 타입 + 타입 가드 패턴), +`ts-no-non-null-assertion`(forbid `(non_null_expression)`), +`ts-type-alias-pascal-case`(naming). TS 컨벤션 7→11 | import 시 자가 검증(bad 플래그+good 통과) 전부 통과 |
| `src/acode/agent/pipeline.py` | 수정 | `acode.pipeline` 로거 + `_emit`/`_verdict_line`/`_emit_verify` 헬퍼. generate: retrieve/rules/synthesize(코드 스냅샷 포함)/verify#N/repair#N/done 이벤트. review: retrieve/rules/verify-input/synthesize/verify-fix/done. `GenerationResult`/`ReviewResult`에 `trace` 필드(+to_dict) | 트레이스 테스트 3개 통과, E2E 로그 확인 |
| `src/acode/cli.py` | 수정 | `acode generate <task> --language L`, `acode review <file>` 서브커맨드. `--verbose`(stderr 실시간 스테이지 로그), `--no-trace`, `--framework/--category/--tag/--context-file/--instruction`. generate는 미검증 시 exit 1 | 실 LLM 실행 시연 완료 |
| `tests/test_ts_conventions.py` | 생성 | 새 룰 6개 검증(enum/const enum/`!`/snake type alias 플래그, as const 대체 코드 전룰 통과, 텍스트 검색으로 룰 회수) + 생성 트레이스(FakeProvider로 enum→as const 수리 전 과정: verify#0 FAIL → repair#1 → verify#1 PASS, 코드 스냅샷 진화, caplog 로그 미러링) + 리뷰 트레이스 스테이지 검증 | 10개 전부 통과 |
| `README.md` | 수정 | Quick start에 `acode check/generate/review --verbose` 단계 추가, 트레이스 설명 | — |
| `docs/tasks/INDEX.md` | 수정 | TASK-0006 등록/완료 처리 | — |

## 결정 사항

- **enum 금지 쿼리는 `(enum_declaration) @bad` 하나로 충분**: tree-sitter-typescript에서
  `const enum`도 같은 노드로 파싱됨을 사전 검증으로 확인 — 별도 룰 불필요.
- **good_example에서 값 객체와 타입을 같은 이름으로 선언** (`const OrderStatus` +
  `type OrderStatus`): TS의 값/타입 선언 병합을 그대로 시연하는 관용 형태.
  패턴 컨벤션에는 `isX` 타입 가드까지 포함 — 실제 E2E에서 LLM이 이 가드 형태를
  그대로 재현함 (RAG 패턴이 생성에 실리는 증거).
- **트레이스는 이중 채널**: 구조화 이벤트(`trace` 배열, 결과 JSON에 항상 포함,
  synthesize 이벤트마다 코드 전문 스냅샷)와 사람용 로그(`acode.pipeline` 로거,
  `--verbose` 시 stderr). 파일 로깅/JSON 핸들러 같은 무거운 장치는 배제 —
  표준 logging이라 소비자가 원하는 핸들러를 붙이면 됨.
- **CLI generate/review는 plain `CodingPipeline` 사용 (ADK 우회)**: 트레이스는
  plain 파이프라인에만 구현. ADK 경로(MCP 서버가 adk 설치 시 선택)는 이벤트
  스트림이 ADK 자체에 있으므로 중복 구현하지 않음 — 필요해지면 백로그.
- **`--no-trace` 옵션**: 트레이스에 코드 스냅샷이 들어가 출력이 커질 수 있어
  끄는 스위치 제공. 기본은 포함 (관찰 가능성이 이 태스크의 목적).

## 검증 결과

- `pytest`: **84 passed, 3 skipped** (기존 74 + 신규 10)
- 코퍼스 재빌드: `acode corpus build --index src/acode` → **105 엔트리**
  (typescript 11로 증가), 룰 19, BM25 용어 976, 에러 0
- 결정적 체크: enum + `!` 든 데모 파일 → `ts-no-enum`(1행), 
  `ts-no-non-null-assertion`(8행) 정확히 검출, exit 1
- **실 LLM E2E ① review** (`acode review bad_enum.ts --verbose`, claude CLI):

  ```
  12:13:39 [retrieve] 8 convention(s) for typescript: ts-pattern-const-object-enum(0.44), ...
  12:13:39 [rules] 9 mechanical rule(s) will be enforced: ..., ts-no-enum, ts-no-non-null-assertion, ...
  12:13:39 [verify-input] FAIL — 2 violation(s): line 1 [ts-no-enum] ...; line 8 [ts-no-non-null-assertion] ...
  12:13:49 [synthesize] LLM (claude-code) wrote a review and a 15-line fix
  12:13:49 [verify-fix] PASS — 9 rule(s) checked, 0 violations
  12:13:49 [done] input_violations=2 fix_verified=True
  ```

  수정본: enum → `as const` 객체 + `type PaymentStatus = typeof PaymentStatus[keyof typeof PaymentStatus]`,
  `!` → null 체크 후 throw. 기계 재검증 PASS.
- **실 LLM E2E ② generate** — 일부러 "Define an **enum** ShipmentStatus..."로 요청:

  ```
  12:14:02 [retrieve] 5 convention(s): ts-no-any(1.00), ts-no-enum(1.00), ..., ts-pattern-const-object-enum(1.00)
  12:14:02 [rules] 4 mechanical rule(s) will be enforced: ...
  12:14:12 [synthesize] LLM (claude-code) produced 27 line(s) of code
  12:14:12 [verify#0] PASS — 4 rule(s) checked, 0 violations
  12:14:12 [done] verified=True after 0 repair iteration(s)
  ```

  enum 요청에도 첫 시도부터 `as const` 객체 + 유도 타입 + `isShipmentStatus`
  타입 가드(패턴 컨벤션의 형태 그대로)로 생성 — 검색된 컨벤션이 프롬프트에
  실려 생성을 교정함. 수리 0회.
- **수리 루프 관찰** (단위 테스트, FakeProvider로 enum 응답 강제):
  트레이스가 `verify#0 FAIL(ts-no-enum) → repair#1 → synthesize → verify#1 PASS`
  를 기록하고, synthesize 스냅샷이 `enum OrderStatus` → `as const` 진화를 보존,
  caplog에 같은 내용의 로그 라인 확인.

## 다음 단계 / 핸드오프

- 트레이스 활용법: `acode generate/review --verbose`(실시간 stderr) 또는 결과
  JSON의 `trace` 배열(각 synthesize 이벤트에 코드 전문 스냅샷). MCP 경유
  `generate_code`/`review_code` 응답에도 plain 파이프라인 사용 시 trace 포함.
- (백로그) ADK 경로에도 동일 트레이스 이벤트 추가 — MCP 서버가 adk 설치 환경에서
  ADK 백엔드를 선택하면 현재는 trace가 빈 배열.
- (백로그) 언어별 시드 확충 계속 (Go/Java/Rust 문법 휠 준비됨).
