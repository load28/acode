# TASK-0016: 룰 공식 페이지 — TanStack Start 프리렌더(SSG) + GitHub Pages 자동 배포

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/rules-discovery-ssr-page-xrh3mn` |
| **시작일** | 2026-07-17 |
| **완료일** | 2026-07-17 |
| **작업 세션** | 세션 1 (2026-07-17) |

## 목표

`conventions/*.json`의 모든 룰을 열람할 수 있는 **공식 문서 페이지**를 만든다.

요구사항:
1. **TanStack Start** 기반, 서버 렌더링된 HTML (요청 시점 SSR은 GitHub Pages에서
   불가능하므로 빌드 시점 프리렌더 = SSG로 충족 — 결정 사항 참조)
2. **"뭘 넣으면 뭘 제안하는지"** 를 쉽게 확인: 각 룰마다 bad example 코드를
   **실제 룰 엔진에 통과시킨 결과**(violation 메시지·위치)를 빌드 시점에 생성해
   페이지에 그대로 보여준다 — 문서와 엔진 동작이 어긋날 수 없음.
3. **한 페이지 = 한 카테고리** (`/category/<category>` 라우트).
4. **룰 추가 시 자동 반영**: 사이트 데이터는 커밋된 파일이 아니라 빌드 때마다
   `conventions/*.json`에서 export 스크립트로 재생성.
5. **GitHub Pages 자동 배포**: main 푸시 시 GitHub Actions가 export → build →
   deploy-pages 까지 수행.

완료 판정(DoD): 로컬에서 `npm run build`가 카테고리별 정적 HTML을 프리렌더하고,
export 스크립트가 전 룰의 엔진 실행 결과를 포함한 데이터를 생성하며,
Pages 배포 워크플로가 저장소에 존재한다. pytest 전체 통과 유지.

## 진행 상황

- [x] 저장소 룰 구조 파악 (4개 언어, 11개 카테고리, 38개 항목)
- [x] 데이터 export 스크립트 (`scripts/export_site_data.py`) — 엔진 실행 결과 포함
- [x] TanStack Start 앱 스캐폴드 (`site/`)
- [x] 카테고리별 페이지 + 인덱스 페이지 + 룰 상세 카드 UI
- [x] 프리렌더(SSG) 설정 + GitHub Pages base path (`/acode/`)
- [x] GitHub Actions Pages 배포 워크플로
- [x] 로컬 빌드 검증 (전 카테고리 HTML 생성 확인)
- [x] pytest 전체 통과 확인

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `scripts/export_site_data.py` | 생성 | conventions/*.json 로드 → 각 rule의 bad/good example을 RuleEngine으로 실제 검사 → 카테고리별로 그룹한 `site/src/data/rules.json` 생성. bad example이 violation을 내지 않거나 good example이 걸리면(문서-엔진 불일치) exit 1 | 검증됨 — 38항목(30 rule + 8 pattern), bad→violation·good→clean 전부 통과 |
| `site/src/router.tsx` | 생성 | TanStack Router 생성, `basepath: import.meta.env.BASE_URL` (vite base와 동기화) | 검증됨 |
| `site/src/data.ts` | 생성 | rules.json 타입 + 접근자 + 카테고리 설명/언어 라벨 | 검증됨 |
| `site/src/styles.css` | 생성 | 다크 문서 테마, bad/good 2단 비교 레이아웃 | 검증됨 |
| `site/tsconfig.json`, `site/.gitignore`, `site/public/.nojekyll` | 생성 | TS 설정 / 빌드 산출물·생성 데이터 커밋 제외 / Pages Jekyll 비활성 | 검증됨 |
| `site/package.json` | 생성 | TanStack Start(react-start 1.132) + React 19 + Vite 7, `predev`/`prebuild`에서 export 스크립트 자동 실행 | 검증됨 |
| `site/vite.config.ts` | 생성 | tanstackStart 플러그인 + `prerender.enabled` + crawlLinks, `base: /acode/` (GH Pages 프로젝트 경로) | 검증됨 |
| `site/src/routes/__root.tsx` | 생성 | 문서 셸(헤더/네비/푸터), 다크 테마 CSS | 검증됨 |
| `site/src/routes/index.tsx` | 생성 | 카테고리 그리드(개수/언어 뱃지) + 안내 | 검증됨 |
| `site/src/routes/category.$slug.tsx` | 생성 | **한 카테고리 = 한 페이지.** 룰 카드: guideline, "이 코드를 넣으면"(bad) → 엔진 violation 출력 → "이렇게 제안"(good). 언어 필터 | 검증됨 |
| `site/src/data/rules.json` | 생성(빌드 산출물) | export 스크립트 산출물. 저장소에는 커밋하지 않고 빌드 시 생성 (`site/.gitignore`) | 검증됨 |
| `.github/workflows/pages.yml` | 생성 | main 푸시 → pip install(엔진) → export → npm build(프리렌더) → actions/deploy-pages | 로컬 문법 검증만 (실배포는 main 머지 후) |

## 결정 사항

- **결정: 요청 시점 SSR 대신 빌드 시점 프리렌더(SSG)** / 이유: GitHub Pages는
  정적 파일만 서빙하므로 Node 서버 SSR이 물리적으로 불가. TanStack Start의
  prerender는 동일한 서버 렌더 코드 경로로 빌드 시 HTML을 생성하므로
  "서버 렌더링된 HTML" 요구를 Pages 제약 안에서 충족. 배제한 대안:
  Vercel/Netlify(요청 시점 SSR 가능하나 "GitHub Pages" 명시 요구 위반),
  CSR SPA(SSR 요구 위반).
- **결정: 사이트 데이터는 빌드 타임 재생성, 커밋 금지** / 이유: "룰 추가 시 자동
  반영" — 데이터 파일을 커밋하면 conventions와 어긋난 채 배포될 수 있음.
  prebuild 훅 + CI에서 항상 재생성하므로 단일 진실원은 conventions/*.json.
- **결정: "뭘 넣으면 뭘 제안하는지"는 실제 엔진 실행 결과로 표시** / 이유: 손으로
  쓴 예상 출력은 엔진 변경 시 썩는다. export 시점에 RuleEngine.check()를 돌려
  violation 메시지·라인을 그대로 싣고, bad example이 violation 0건이면 빌드를
  실패시켜 문서-엔진 불일치를 CI에서 잡는다. (tsx 룰은 tsx 다이얼렉트로 검사)
- **결정: 카테고리는 언어 통합, 페이지 내 언어 필터 제공** / 이유: "한 페이지 =
  한 카테고리" 요구. naming처럼 여러 언어에 걸친 카테고리는 언어 뱃지+필터로 구분.
- **결정: `kind: pattern` 항목은 권장 패턴 섹션으로 표시** / 이유: pattern은
  기계 검사 룰이 없어 violation 데모가 불가 — good example만 보여준다.

## 검증 결과

- `python scripts/export_site_data.py` → `38 entries (30 rules, 8 patterns), 11 categories` (bad→violation 0건 또는 good→violation 발생 시 exit 1 — 전부 통과)
- `cd site && npm run build` → **13 페이지 프리렌더** (`/` + 11개 카테고리, base `/acode/`), 산출물 `dist/client/`
- SSR 확인: `dist/client/category/correctness/index.html`에 "No var declarations" 3회, "var is forbidden; use const or let" 포함. types 페이지에 "엔진이 이렇게 제안" 박스 12개(= types 룰 12개) 서버 렌더 확인
- 정적 서빙 스모크: `/acode/`를 루트로 http.server 기동 → `/acode/` title OK, `/acode/category/types/` 엔진 메시지 OK, CSS 에셋 200
- `pytest` → **198 passed, 3 skipped**

## 다음 단계 / 핸드오프

- main 머지 후 저장소 Settings → Pages → Source를 **GitHub Actions**로 설정해야
  첫 배포가 동작한다 (저장소 설정은 코드로 불가 — 소유자 1회 작업).
- 배포 URL: `https://load28.github.io/acode/`
- 룰 추가 절차는 기존과 동일 (conventions/*.json 수정) — 머지되면 페이지 자동 갱신.
