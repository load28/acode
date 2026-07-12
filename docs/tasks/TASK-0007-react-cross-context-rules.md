# TASK-0007: React 크로스 컨텍스트 시맨틱 룰 (prop drilling + 상태 출처 분석)

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/react-prop-drilling-rules-8vno8j` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | 세션 1 |

## 목표

기존 룰 엔진은 "한 파일 + tree-sitter 쿼리 하나"로 판단하는 룰만 지원한다.
React에서는 **여러 지점을 함께 봐야** 판단 가능한 컨벤션이 많다:

1. **서버 상태 prop drilling**: 데이터 prop이 3단계 이상 내려가는데 그 출처가
   서버 fetch(useEffect + fetch/axios → setState)라면 → React Query 등
   서버 상태 라이브러리를 써야 한다.
2. **전역 변경 의도**: useState의 value+setter 쌍이 여러 브랜치로 퍼지거나
   setter가 깊게 드릴링되면(자손이 조상 상태를 변경) → Context를 써야 한다.
3. **일반 prop drilling**: 출처와 무관하게 중간 컴포넌트가 소비하지 않고
   그대로 전달만 하는 체인이 길면 경고.

이 판단을 **LLM 없이 기계적·결정적으로** 내린다 (같은 입력 → 항상 같은 판정).

**Definition of Done**
- [x] 멀티파일 React 프로젝트 분석기 (컴포넌트/훅/렌더 엣지/prop 체인)
- [x] `semantic` 룰 타입: 등록된 분석기 + 파라미터, 기존 store에 저장·자체검증 가능
- [x] 시드 룰 3종 (`conventions/react.json`) — bad/good 멀티파일 예제로 자체검증
- [x] MCP `check_project` 툴 + CLI `acode check-project`
- [x] 테스트 전부 통과 (기존 + 신규)

## 진행 상황

- [x] 저장소/엔진 구조 파악 (rules.py, store.py, steps.py, server.py, cli.py)
- [x] React 분석기 구현 (`astcore/react.py`)
- [x] `semantic` 룰 타입 엔진 통합 (`astcore/rules.py`)
- [x] 시드 컨벤션 `conventions/react.json`
- [x] MCP 툴 + CLI 서브커맨드
- [x] 테스트 작성 + 전체 통과 확인
- [x] 문서/INDEX 갱신 + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/react.py` | 생성 | JSX/TSX 프로젝트 결정적 분석기: 컴포넌트 추출(대문자 함수+JSX), 훅 바인딩(useState/useEffect/useQuery/useContext/useReducer), fetch-in-effect로 서버 출처 판정, 렌더 엣지, import 해석, prop 전달 체인(DFS), 시맨틱 체커 3종 + 레지스트리, `// @file:` 가상 멀티파일 분리 | 45개 테스트로 검증 |
| `src/acode/astcore/rules.py` | 수정 | `semantic` 룰 타입 추가 (`check`=체커 이름, `params`=임계값), `RuleViolation.file` 필드, `RuleEngine.check_project(files, ...)` 신설, 단일 문자열 check는 가상 파일 분리로 시맨틱 룰 지원. 추가로 tsx↔typescript 구조 룰 호환(`_rule_applies`): .tsx 파일에도 ts-no-var 등 구조 룰 적용, 단 naming 룰은 제외 | 기존 테스트 영향 없음 |
| `src/acode/astcore/__init__.py` | 수정 | `analyze_project`, `semantic_check_names` 등 export | - |
| `conventions/react.json` | 생성 | 시드 시맨틱 룰 3종 (server-state drilling→React Query, shared mutable state→Context, generic prop drilling) — 멀티파일 bad/good 예제 포함, insert 시 자체검증됨 | import 시 self-verify 통과 |
| `src/acode/mcpserver/server.py` | 수정 | `check_project` 툴 추가 (디렉터리/파일 스캔 → 크로스 파일 시맨틱 + 단일파일 룰 동시 검사) | 테스트로 검증 |
| `src/acode/cli.py` | 수정 | `acode check-project <path>` 서브커맨드 | 테스트로 검증 |
| `src/acode/rag/corpus.py` | 확인만 | conventions/*.json glob이라 react.json 자동 포함 | 수정 불요 |
| `README.md` | 수정 | semantic 룰 타입·check-project·MCP 툴 문서화 | - |
| `CLAUDE.md` | 수정 | 저장소 구조 요약에 react.py 반영 | - |
| `tests/test_react_rules.py` | 생성 | 분석기 단위(컴포넌트/훅/출처/체인) + 룰 판정(경계값 2단계/3단계, useQuery 예외, Context 예외) + store 자체검증 + MCP/CLI 통합 | 45 passed |

## 결정 사항

- **시맨틱 룰 = 등록된 체커 이름 + params**: tree-sitter 쿼리로는 크로스 파일
  판단이 불가능. 임의 코드 실행(eval) 방식은 결정성·안전성 훼손이라 배제.
  체커는 코드에 살고, 컨벤션(임계값·메시지·예제)은 데이터로 store에 산다.
- **멀티파일 예제는 `// @file: path` 마커**: store 스키마(good/bad_example TEXT)를
  건드리지 않고 자체검증을 유지하기 위함. DB 스키마 변경 대안은 배제.
- **체인 추적은 보수적**: 식별자/멤버 접근 그대로 전달(`data`, `data.items`,
  `{...props}` 스프레드)만 체인으로 인정. 함수 호출로 변형된 값은 체인 단절로
  처리 — 거짓 양성(false positive)보다 놓침이 낫다는 판단.
- **서버 출처 판정**: useEffect 콜백 안에서 fetch류 호출(`fetch`, `axios`,
  params로 확장 가능)과 같은 effect에서 호출된 setter의 state만 server-state로
  분류. `.then(setData)` 같은 콜백 참조 전달도 잡음.
- **깊이 의미**: depth = 값이 컴포넌트 경계를 넘은 횟수(엣지 수). 기본
  `max_depth: 3` = "3단계 이상 내리면".
- **useQuery/useContext 사용 시 자동 통과**: 출처 분류가 `query`/`context`가
  되므로 룰 대상 자체가 아님 — 별도 예외 처리 불필요한 설계.
- **tsx↔typescript 룰 호환은 구조 룰만**: .tsx 파일에 typescript 구조 룰
  (forbid/require/require_in)을 tsx 문법으로 재컴파일해 적용. naming 룰은
  제외 — React 컴포넌트는 PascalCase 함수라 `ts-func-camel-case`가 전부
  오탐하는 것을 데모에서 확인하고 선을 그음. 컴파일 안 되는 쿼리는 해당
  파일에서 조용히 스킵 (실패 아님).

## 검증 결과

- `pytest` — **134 passed, 2 skipped** (기존 89 + 신규 45 `test_react_rules.py`;
  스킵 2는 기존 옵션 엔진 의존성), 0 failed
- `acode import conventions/react.json` — 3 convention(s) self-verified & imported
- `acode check-project` E2E 데모 (bad 프로젝트, exit=1) — 위반 5건:
  - `App.tsx:6 [react-server-state-drilling]` 체인
    `App -[user]-> Layout -[user]-> Sidebar -[user]-> UserCard` (fetch 출처)
  - `App.tsx:7 [react-shared-mutable-state]` filter value+setter가
    Layout/Toolbar 2개 브랜치로 팬아웃
  - `App.tsx:6,7 [react-prop-drilling]` warning 2건 (rename `filter→label` 추적 포함)
  - `UserCard.tsx:2 [ts-no-var]` — typescript 구조 룰이 .tsx에서 동작
  - naming 룰(`ts-func-camel-case`)은 컴포넌트를 오탐하지 않음 (호환 제외 확인)
- good 프로젝트 (React Query + Context 사용) — passed, 0 violations, exit=0

## 다음 단계 / 핸드오프

- 후속 아이디어(백로그 승격 후보): barrel re-export(`index.ts`) 해석,
  `React.memo`/`forwardRef` 래핑 인식, Zustand/Jotai 등 전역 스토어 출처 분류,
  monorepo 별칭(import alias) 해석.
