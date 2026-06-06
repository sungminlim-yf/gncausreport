---
name: audience-fit
description: 대상 채널의 독자 카드(audience/briefs/<채널>)를 읽어 보고서의 톤·깊이·강조점·피할 것을 맞춤화한다. 원본(profiles)이 아닌 간추린 brief를 매 실행 시 저렴하게 로드한다(progressive disclosure). writer 단계에서 사용한다.
---

# audience-fit — 독자 맞춤

독자-정보 적합성을 극대화하기 위해 **원본(profiles)과 요약(briefs)을 분리**하고, 매 실행 시 가벼운 **brief**를 먼저 참조한다.

## 동작
1. 대상 채널 이름(예: `exec-team`)으로 **`audience/briefs/<채널>.md`** 독자 카드를 읽는다.
2. 카드의 항목을 글쓰기에 반영한다:
   - **누구**: 독자의 역할·맥락
   - **관심**: 강조할 주제·각도
   - **깊이**: 요약 중심 / 근거 상세 등
   - **톤**: 간결·단정 / 친근 등
   - **피할 것**: 장황한 배경, 미검증 추측 등 금기
3. content-production 양식 위에 이 맞춤을 덧입힌다.

## 2폴더 원리 (progressive disclosure)
- `audience/profiles/<채널>.md` = **풍부한 원본**(사람이 관리). 부서·역할, 의사결정 맥락, 금기, 선호 길이/톤 등.
- `audience/briefs/<채널>.md` = **5~10줄 독자 카드**(빠른 참조). 매 실행 시 이것만 로드.
- profiles가 갱신되면 운영자가 **`/refresh-brief <채널>`**(D15)로 brief를 재간추린다 — brief 낙후 방지. brief는 자동으로 낡지 않으므로 이 명령이 유일한 갱신 경로다.

## 유지보수 시 (refresh-brief가 호출할 때)
- `profiles/<채널>.md` 원본을 읽어 **누구·관심·깊이·톤·피할 것** 5항목으로 압축한 새 카드를 `briefs/<채널>.md`에 쓴다.
- 카드는 사람이 5초 안에 읽도록 짧게 유지한다.
