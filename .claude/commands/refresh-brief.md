---
description: audience/profiles/<채널> 원본에서 audience/briefs/<채널> 독자 카드를 다시 간추린다(유지보수). profiles 수정 후 운영자가 명시 실행.
argument-hint: <대상채널>
---

# /refresh-brief — 독자 카드 재간추림 (D15)

입력: `$ARGUMENTS` (대상채널, 예: `exec-team`)

brief는 자동으로 낡지 않으므로, profiles가 갱신되면 이 명령이 **유일한 갱신 경로**다.

## 절차
1. `audience/profiles/<대상채널>.md` 원본을 읽는다(없으면 안내 후 중단).
2. `audience-fit` 스킬 규칙에 따라 **5항목**으로 압축한다:
   - **누구**(역할·맥락) / **관심**(주제·각도) / **깊이** / **톤** / **피할 것**
3. `audience/briefs/<대상채널>.md`에 5~10줄짜리 독자 카드로 덮어쓴다(사람이 5초에 읽을 분량).
4. 변경 요약(무엇이 바뀌었는지)을 한 줄로 출력.

> brief는 매 `/brief` 실행 시 저렴하게 로드되는 빠른 참조본이다(progressive disclosure). 원본의 민감 정보(D23)는 brief로 과도하게 옮기지 않는다.
