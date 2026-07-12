# TASK-0008: React 분석기 확장 — 변형(파생 값) 추적 + 커스텀 훅 투과

| | |
|---|---|
| **상태** | done |
| **브랜치** | `claude/react-prop-drilling-rules-8vno8j` |
| **시작일** | 2026-07-12 |
| **완료일** | 2026-07-12 |
| **작업 세션** | 세션 1 (TASK-0007 직후 이어짐) |

## 목표

TASK-0007 분석기의 두 가지 실전 구멍을 메운다:

1. **변형 추적**: `const rows = transform(data)` / `<Child items={data.filter(...)}/>`
   처럼 값이 변형되면 체인이 끊겼다. 파생 값이 원본의 출처(server-state 등)를
   상속하고, prop 체인도 변형을 넘어 이어지게 한다. 콜백 래핑
   (`onChange={(v) => setFilter(v)}`)도 setter 전달로 인식한다.
2. **커스텀 훅 투과**: `useUser()` 안에 useState+useEffect+fetch를 감추고
   `{ user }`를 리턴하면 출처 불명이 됐다. 실전 코드는 대부분 훅으로 감싸므로
   훅 정의를 분석해 리턴 값의 출처를 호출 지점으로 전파한다. 훅→훅 호출,
   파일 간 import, `useLoad(setUser)`(setter를 훅에 넘겨 채우는 패턴)까지.

기존 시드 룰 3종은 그대로 — 분석기가 좋아지면 룰이 자동으로 더 많이 잡는다.

**Definition of Done**
- [x] 파생 바인딩: 선언 초기화식/useMemo/구조분해에서 출처 상속 (고정점)
- [x] JSX 인라인 변형·콜백 래핑에서 후보 식별자 수집 → 체인 시작/전달
- [x] 훅 정의 분석 + 리턴 매핑(객체/단일/배열/훅 재호출) + 호출 지점 전파
- [x] setter를 훅 인자로 넘기는 패턴에서 state를 server-state로 승격
- [x] `Context.Provider`로 넘기는 값은 집계 제외 (승인된 패턴 오탐 방지)
- [x] 기존 테스트 + 신규 테스트 전부 통과, 시드 룰 자체검증 유지

## 진행 상황

- [x] react.py 재구성 (2단계 분석: 파일별 추출 → 프로젝트 해석)
- [x] 파생 값 고정점 + prop_roots 기반 체인 전달
- [x] HookFacts / HookCall / 리턴 스펙 + 훅 고정점
- [x] 테스트 확장 + 전체 통과
- [x] 문서/INDEX 갱신 + 커밋/푸시

## 파일별 작업 기록

| 파일 | 작업 | 내용 | 결과 |
|---|---|---|---|
| `src/acode/astcore/react.py` | 재작성 | Binding에 `derived_from`/`prop_roots`/`origin_root` 추가, 파생 출처 고정점(`_resolve_derived`), PropPass가 후보 식별자 튜플(`sources`) 보유, 훅 정의 추출(HookFacts)·리턴 스펙·훅 고정점(`_resolve_hooks`)·호출 지점 전파(`_apply_hook_call`)·서버 기록 파라미터(`server_write_params`), Provider 스킵, 파생 홉 `~[prop]~>` 표기 | 전체 테스트로 검증 |
| `tests/test_react_rules.py` | 수정+확장 | 파생/훅 시나리오 테스트 클래스 2개 추가, `call breaks provenance` 테스트를 새 의미(파생 전달)로 교체 | 통과 |
| `README.md` | 수정 | 파생 추적·커스텀 훅 투과 한 줄 반영 | - |

## 결정 사항

- **파생 출처는 우선순위 상속**: 여러 원본을 섞으면(`merge(user, filter)`)
  `server-state > query > context > local-state > dispatch > setter > prop > local`
  순으로 가장 강한 출처 하나를 상속하고 체인도 그 식별자로 시작한다
  (체인 폭발 방지). prop 뿌리(`prop_roots`)는 전부 합집합으로 유지해
  전달 판정에는 모두 쓴다.
- **콜백 래핑은 전달로 본다**: `onChange={(v) => setFilter(v)}`의 setFilter는
  setter 전달이다. 호출되는 식별자도 후보에 포함한다 — 처음엔 제외했다가
  이 케이스가 안 잡혀 철회 (`transform(x)`의 transform 같은 미지 함수는
  바인딩이 없어 체인 단계에서 자연히 걸러지므로 잡음이 안 됨). 화살표
  함수의 매개변수와 중첩 JSX(자체 렌더 엣지로 이미 잡힘)는 제외.
- **`X.Provider`로 넘기는 값은 제외**: Context Provider에 value를 넣는 것이
  바로 권장 패턴이므로 체인/팬아웃 집계 대상이 아니다 (이걸 안 빼면
  good_example이 자기 룰에 걸린다).
- **훅 해석은 유계 고정점**: 훅→훅 호출/순환 대비 `len(hooks)+2`회 반복 후
  수렴 안 하면 그대로 확정 (결정적). 리턴은 마지막 return 문 기준,
  중첩 함수 내부 return은 제외.
- **훅 인자 승격은 위치 기반**: 훅 파라미터가 fetch성 effect에서 참조되면
  `server_write_params`로 기록, 호출부에서 그 위치 인자가 setter면 짝 state를
  server-state로 승격. 훅이 자기 파라미터를 다른 훅에 넘기는 것도 전이.

## 검증 결과

- `pytest` — **154 passed, 2 skipped** (신규 20개: 파생 9 + 훅 10 + 추출 1), 0 failed
- 시드 3종 `conventions/react.json` 자체검증 계속 통과 (수정 없음)
- E2E 데모: `useUser()` 커스텀 훅(파일 분리) + `transform()` 변형 + 콜백 래핑
  조합에서 `react-server-state-drilling` 검출 확인 — 메시지:
  `'rows' (derived from 'user') is server state ... App -[rows]-> Layout
  -[rows]-> Sidebar ~[data]~> Grid` (파생 홉 `~[..]~>` 표기)

## 다음 단계 / 핸드오프

- 남은 아이디어는 INDEX 백로그 항목(TASK-0007 핸드오프)과 동일: barrel
  re-export, `React.memo`/`forwardRef`, Zustand/Jotai, import alias.
- 훅 리턴이 조건 분기로 갈리는 경우(마지막 return만 봄)는 알려진 근사.
