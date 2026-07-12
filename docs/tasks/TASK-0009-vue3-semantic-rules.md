# TASK-0009: Vue 3 시맨틱 룰 — React 크로스 컨텍스트 패턴을 SFC로 이식

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/react-prop-drilling-rules-8vno8j` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | 세션 1 (TASK-0007/0008에 이어) |

## 목표

TASK-0007/0008의 크로스 파일 시맨틱 판정(프롭 드릴링 깊이 + 데이터 출처)을
**Vue 3 Composition API**(`<script setup>` SFC)에서도 쓸 수 있게 한다.
체인/체커/훅 고정점 등 프로젝트 모델 계층은 재사용하고, 추출 계층만
Vue 문법으로 새로 만든다. 개념 대응:

| React | Vue 3 |
|---|---|
| 컴포넌트 함수 + JSX | `.vue` SFC (`<script setup>` + `<template>`) |
| props 구조분해 | `defineProps` (타입 인자 / 객체 / 배열 / withDefaults) |
| useState | `ref` / `reactive` (setter 없음 — `.value` 직접 변이) |
| useEffect + fetch | `onMounted`/`watchEffect`/`watch` + fetch, top-level `.then` |
| useQuery | vue-query `useQuery` / Nuxt `useFetch`/`useAsyncData` / `useSWRV` |
| useContext | `inject` (Provider 대응 = `provide` — 템플릿 엣지가 아니라 자연 제외) |
| 커스텀 훅 | 컴포저블 (`use*` 함수, `.ts` 파일 포함) |
| setter 드릴링 | `v-model` / `@update` 이벤트로 아래에서 변이 (mutation edge) |
| `<Child data={x}/>` | `<Child :data="x"/>` (kebab-case 태그/속성 정규화) |

**Definition of Done**
- [x] `.vue` SFC 분리(라인 보존) + `vue` 의사 언어 등록 (스크립트 = TS 문법)
- [x] 스크립트 추출: defineProps 3형태+withDefaults, ref/reactive/computed/inject,
      쿼리 컴포저블, fetch성 스코프의 **할당 대상** 추적, 컴포저블(훅 기계 재사용)
- [x] 템플릿 스캐너: 컴포넌트 태그, `:prop`/`v-bind`, `v-model`, `@listener`
      (할당/변이 함수 → mutation edge), 빌트인 태그 스킵
- [x] Vue 체커 3종 등록 + 시드 `conventions/vue.json` (자체검증)
- [x] check_project가 혼합 프로젝트에서 언어별 시맨틱 분석 분리 실행
- [x] 테스트 전부 통과

## 진행 상황

- [x] tsx 문법으로 Vue 구조 파싱 확인 (defineProps 타입 인자 등)
- [x] parser에 vue 의사 언어 (script-only view로 라인 보존)
- [x] astcore/vue.py 구현
- [x] rules.py 언어별 시맨틱 디스패치
- [x] 시드 컨벤션 + 테스트
- [x] 문서/INDEX + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/flow.py` | 생성 | **프레임워크 중립 코어 추출**(사용자 지시: 뷰를 리액트 모듈에 넣지 않기): 데이터 모델(Binding/PropPass/ComponentFacts/HookFacts/PropChain), 파생 고정점, 훅 고정점(인자 승격 정책은 프레임워크별 콜백), 체인 빌더, 중립 체커(server-drilling/prop-drilling), 체커 레지스트리 | 리팩터 후 기존 154개 테스트 그대로 통과 (동작 보존 확인) |
| `src/acode/astcore/react.py` | 축소 | 중립 부분을 flow.py로 이관, React 전용(JSX/useState/setter 정책/Provider 스킵/shared-mutable 체커)만 잔류. Vue 코드 0줄 | - |
| `src/acode/astcore/vue.py` | 생성 | SFC 블록 분리(오프셋 계산), `script_only_view`(라인 보존), defineProps/상태/컴포저블 추출, fetch성 스코프 할당 대상 승격, 자체 템플릿 토크나이저(따옴표/주석 인지), v-model·이벤트 변이 엣지, Vue 체커(`vue-shared-mutable-state` 신규 + server-drilling/prop-drilling은 React 체커 재사용 등록) | 테스트로 검증 |
| `src/acode/astcore/parser.py` | 수정 | `vue` 의사 언어: 문법=typescript, `parse()`가 script-only view 파싱(비스크립트 줄은 공백화해 위치 보존), `.vue` 확장자 매핑 | 기존 테스트 영향 없음 |
| ~~react.py에 Vue 필드 추가~~ | 되돌림 | 처음에 `mutation_edges`/ref 승격을 react.py에 넣었다가 사용자 지시로 철회 — `mutation_edges`는 vue.py의 `VueComponentFacts` 서브클래스로, ref 승격은 vue.py의 `_apply_composable_call` 정책으로 분리. `_resolve_named`의 stem 비교 수정(`./X.vue` import 해석)만 중립 버그픽스로 flow.py에 반영 | - |
| `src/acode/astcore/rules.py` | 수정 | 시맨틱 허용 언어에 vue 추가, 언어별 분석기 디스패치(`_semantic_analysis`), `check_project`가 파일에서 감지된 언어의 시맨틱 룰도 실행(언어별 분석 분리: react 분석엔 react 계열 파일만, vue 분석엔 .vue/.ts/.js만), tsx↔ts 호환에 vue↔ts 추가 | 테스트로 검증 |
| `src/acode/agent/steps.py` | 수정 | 프로젝트 스캔 확장자에 `.vue` 추가 | - |
| `conventions/vue.json` | 생성 | 시드 3종 (server-state→vue-query/Pinia, shared mutable→provide/inject, prop drilling) — 멀티파일 SFC 예제로 자체검증 | import 통과 |
| `tests/test_vue_rules.py` | 생성 | SFC 분리/템플릿 스캐너/스크립트 추출 단위 + 체인/룰 판정 + 컴포저블 투과 + store/CLI 통합 | 통과 |

## 결정 사항

- **템플릿 파서는 자체 토크나이저**: tree-sitter-html 휠 추가 대신 ~80줄
  토크나이저(따옴표 안 `>` 처리, 주석 스킵). Vue 디렉티브는 어차피 HTML
  문법이 몰라서 속성 문자열 해석은 자체로 해야 함 — 의존성 추가 이득이 적음.
  속성 값 표현식은 typescript 문법으로 파싱해 기존 후보 수집기 재사용.
- **`vue`는 의사 언어**: 문법=typescript, `parse()`가 스크립트 외 줄을
  공백으로 치환한 뷰를 파싱 → 단일 파일 쿼리 룰(ts-no-var 등)도 .vue에서
  올바른 라인 번호로 동작. naming 룰은 React 때와 같은 이유로 전이 제외.
- **변이 모델**: Vue는 setter가 없으므로 fetch성 스코프에서 **할당 대상**
  (`x.value = ...`, `x = ...`)만 server-state로 승격 (React의 "참조된 setter"
  보다 엄격 — `watch(user, () => fetch(url(user.value)))`처럼 fetch의 입력으로
  쓰인 ref를 오탐하지 않기 위함).
- **아래→위 변경 = mutation edge**: 자식 태그의 `v-model`, 그리고
  `@evt="x = $event"` 또는 상태를 할당하는 스크립트 함수를 넘기는 리스너를
  변이 엣지로 기록. `vue-shared-mutable-state`는 (변이 엣지 존재) AND
  (팬아웃 ≥ min_branches 또는 값 체인 깊이 ≥ max_depth)일 때 발동.
- **provide는 자연 제외**: provide()는 템플릿 렌더 엣지가 아니므로 체인에
  아예 안 잡힘 — React의 Provider 스킵 같은 특례가 필요 없음.
- **체커 재사용**: server-drilling/prop-drilling은 flow.py의 **중립 체커**를
  `vue-*` 이름으로 등록 (동일 ProjectAnalysis 모델 위에서 동작).
  shared-mutable만 프레임워크별 구현(React=setter 짝, Vue=mutation edge).
- **혼합 프로젝트**: check_project가 시맨틱 룰을 언어별로 그룹핑, react
  분석에 .vue를 넣지 않고(tsx 파서가 HTML을 못 읽음) vue 분석에 tsx를 넣지
  않음. 단일 파일 룰은 기존 per-file 매칭 유지.

## 검증 결과

- `pytest` — **188 passed, 2 skipped** (신규 34: SFC 레이아웃 3 + 템플릿
  스캐너 3 + 스크립트 추출 9 + 체인·룰 9 + 컴포저블 3 + 통합 7), 0 failed
- 리팩터 중간 검증: flow.py 추출 직후(vue.py 작성 전) 기존 154개 전부 통과
  — 코어 이동이 React 동작을 바꾸지 않음을 먼저 고정
- `acode import conventions/vue.json` — 3 convention(s) self-verified & imported
- E2E: 4개 `.vue` + 컴포저블 `.ts` 데모에서 `vue-server-state-drilling`
  (컴포저블 안 onMounted+fetch, 3단계), `vue-shared-mutable-state`(v-model
  팬아웃), `vue-prop-drilling`, `ts-no-var`(.vue 스크립트, vue↔ts 호환) 동시
  검출 — good 프로젝트(vue-query + provide/inject) 0건. 마지막 홉이
  `summarize(user)` 변형이어도 체인 유지(0008 파생 추적이 Vue에서도 동작)

## 다음 단계 / 핸드오프

- Pinia 스토어(`useXxxStore`) 출처 분류(현재 미지 컴포저블 → local),
  Options API(`export default { ... }`) 미지원, `defineModel()`(3.4+),
  `defineEmits` 기반 emit 릴레이 체인(상향 전파 깊이) — 백로그.
- 훅/컴포저블 리턴은 마지막 return 기준(0008과 동일 근사).
