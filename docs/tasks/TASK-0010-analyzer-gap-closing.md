# TASK-0010: 분석기 한계 극복 — 스토어 출처, Options API, emit 릴레이, 리턴 병합, barrel/memo

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/react-prop-drilling-rules-8vno8j` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | 세션 1 (TASK-0007~0009에 이어) |

## 목표

TASK-0007~0009에서 백로그로 남긴 한계들을 기계적(결정적) 해법으로 제거한다:

1. **스토어 출처** (`store` 출처 종류 신설 — 승인된 전역 상태):
   - Pinia: `defineStore`(옵션/셋업 스타일 모두) 정의 해석 → `useXxxStore()`
     리턴 값 전부 `store`. 미해석 `use*Store` 호출도 휴리스틱으로 `store`.
     `storeToRefs(store)` 구조분해 전파.
   - React: `create()`(Zustand)로 정의된 `use*` 훅 → `store`,
     `useAtom`(Jotai, [값,세터] 쌍)/`useAtomValue`/`useSetAtom`/`useSelector` → `store`.
   - 의미: server-drilling/Context 룰에서 자동 면제(이미 올바른 곳에 있음),
     generic prop-drilling에는 여전히 잡힘(스토어면 쓰는 곳에서 읽어라).
2. **컴포저블/훅 리턴 병합**: 마지막 return만 보던 것을 모든 자체 return의
   합집합으로 (조건 분기 early-return 대응; 같은 키 충돌 시 마지막 우선).
3. **defineModel() (Vue 3.4+)**: prop 등록으로 매핑 (`modelValue` 또는 명명).
4. **emit 릴레이 체인**: prop 체인의 거울상 — 자식 태그 리스너가
   `$emit('x', ...)`(또는 emit 호출 함수)로 재방출하면 릴레이 엣지.
   상향 그래프에서 릴레이 깊이 ≥ N이면 신규 룰 `vue-emit-relay` 발동.
5. **Options API**: `export default {...}` / `defineComponent({...})` —
   props/data/computed/methods/라이프사이클(`this.x` 추적, fetch 승격)/
   setup() 통과.
6. **barrel re-export**: `export { X } from './x'` / `export * from './x'`
   따라가기 (유계 깊이 3). **React.memo/forwardRef** 래핑 언래핑.

**극복 불가로 남는 것** (정적 분석의 원리적 한계 — 문서화로 대응):
동적 컴포넌트(`<component :is="변수">`), 런타임 문자열 키 provide/inject,
번들러 별칭의 실제 매핑(현재는 stem 매칭 폴백으로 대부분 커버),
v-for 변수 섀도잉. 결정성 우선 원칙상 추측하지 않고 체인을 끊는다.

## 진행 상황

- [x] flow: `store` 출처, 리턴 병합, re-export 추적, HookFacts.frozen
- [x] react: memo/forwardRef 언래핑, Zustand/Jotai/useSelector
- [x] vue: defineModel, Pinia(defineStore/storeToRefs/휴리스틱), emit 릴레이,
      Options API
- [x] 신규 시드 룰 `vue-emit-relay` + vue.json 갱신
- [x] 테스트 + 전체 통과
- [x] 문서/INDEX + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/flow.py` | 수정 | `store` 출처(우선순위 context 다음, 체인 시작 가능), `extract_returns_spec`가 모든 자체 return 병합, `extract_reexports` + `FileFacts.reexports/star_reexports`, `resolve_named`가 barrel을 유계 깊이 3으로 추적, `HookFacts.frozen`(스토어 훅 리턴 고정) | 테스트로 검증 |
| `src/acode/astcore/react.py` | 수정 | `memo`/`forwardRef`(중첩 포함) 언래핑 후 컴포넌트 인식, Zustand `create()` 정의 → frozen store 훅, `useAtom` 쌍/`useAtomValue`/`useSetAtom`/`useSelector`/`useStore` → store 출처 | 테스트로 검증 |
| `src/acode/astcore/vue.py` | 수정 | `defineModel` → prop 등록, Pinia `defineStore`(옵션: state/getters 키, 셋업: 컴포저블 분석 후 출처를 store로 강제) → frozen 훅, 미해석 `use*Store` 휴리스틱, `storeToRefs`, emit 릴레이(defineEmits/emit 호출/`$emit` 릴레이 엣지 + 상향 체인 + `vue-emit-relay` 체커), Options API(props/data/computed/methods/라이프사이클 `this.x`/setup 통과) | 테스트로 검증 |
| `conventions/vue.json` | 수정 | `vue-emit-relay` 시드 룰 추가 (릴레이 2단계 이상 → v-model 적정 레벨/provide 콜백/스토어) | 자체검증 통과 |
| `tests/test_react_rules.py` | 확장 | memo/forwardRef, Zustand/Jotai store 출처, barrel re-export, 리턴 병합 | 통과 |
| `tests/test_vue_rules.py` | 확장 | defineModel, Pinia 양 스타일+휴리스틱, storeToRefs, emit 릴레이(발동/비발동), Options API(props/data/fetch 승격/methods 변이/computed) | 통과 |

## 결정 사항

- **store는 '승인된 전역'**: `context`와 동급 취급 (우선순위 context 바로 다음).
  server-drilling(서버 상태만)·shared-mutable(local-state만) 자동 면제,
  generic drilling에는 노출 — 스토어 값을 3단계 내리면 "쓰는 곳에서 스토어를
  읽어라"는 경고가 여전히 유효하므로.
- **Pinia 셋업 스토어는 출처를 store로 강제**: 스토어 안에서 fetch를 하든
  ref든, 소비자 입장에선 "스토어에서 읽는 값" — 내부 구현은 스토어의 책임.
- **frozen 훅**: 스토어 훅의 리턴은 고정점에서 재계산하지 않는다
  (`HookFacts.frozen`) — create()/defineStore가 이미 의미를 결정.
- **리턴 병합 규칙**: object 리턴이 하나라도 있으면 object로 병합(키 합집합,
  같은 키는 마지막 return 우선 — 최종 상태가 대표), 아니면 마지막
  identifier/array/call. early-return `if (loading) return { user: null }`
  패턴이 주 대상.
- **emit 릴레이 깊이 기본 2**: 릴레이 1번(자식→부모→조부모 소비)은 통상
  패턴, 2번 이상 중계부터 "이벤트 버킷 릴레이" 냄새로 판단.
- **Options API는 핵심 옵션만**: mixins/extends/인젝션 옵션은 범위 외(문서화).
  `defineComponent(...)` 래핑과 `setup()` 메서드는 지원.

## 검증 결과

- `pytest` — **211 passed, 2 skipped** (신규 23: react 8 `TestGapClosing` +
  vue 15 `TestVueGapClosing`), 0 failed
- `conventions/vue.json` 4종(신규 vue-emit-relay 포함) self-verify 통과
- E2E(단일 프로젝트에 혼합): Pinia 셋업 스토어 값 3단계 드릴링 →
  `vue-prop-drilling`만 발동(`(store)` 표기, 서버 룰 면제 확인), Grid의
  `$emit('save')`가 Panel→Layout 2단 릴레이 → `vue-emit-relay` 발동
  (`Grid =(save)=> Panel =(save)=> Layout`), Options API `mounted()+fetch`는
  드릴링이 없어 정확히 침묵(드릴링 시 발동은 단위 테스트로 검증)

## 다음 단계 / 핸드오프

- 남은 원리적 한계는 목표 절에 문서화 (동적 컴포넌트, 런타임 키 등).
- tsconfig paths 별칭의 정확 해석(현재 stem 폴백) — 필요 시 별도 태스크.
