---
description: 최근 명함 인사이트(contacts/)를 종합해 '신규 컨택 → GNC 호주 사업 함의' 보고서를 만들고 게시한다. 웹 검색 없이 내부 메모만 사용.
argument-hint: [대상채널=exec-team] [--days 21] [--publish]
---

# /contacts-brief — 명함 네트워크 인사이트 보고

`businesscard` 파이프라인이 쌓아둔 `contacts/` 메모를 종합해, **최근 만난 인물들이
GNC 호주 비상발전기 사업에 주는 시사점·기회**를 정리한 보고서를 만든다.
일반 `/brief` 와 달리 **웹 검색(researcher) 단계가 없다** — 입력은 내부 메모뿐.

입력: `$ARGUMENTS` (형식: `[대상채널] [--days N] [--publish]`, 기본 `exec-team`, `--days 21`)

## 1. 입력 수집
1. `contacts/_recent-digest.md` 를 읽는다(최근 명함 인사이트 종합본).
   - 없거나 비었으면, `python ../businesscard/contacts_digest.py --days <N>` 로 생성 시도.
   - 그래도 최근 메모가 0건이면 **중단**하고 "최근 신규 컨택 없음"으로 운영자에 한 줄 보고.
2. `facts.md`(회사 사실) 와 `audience/briefs/<대상채널>.md`(독자 맥락) 를 읽는다.

## 2. writer (메인 + content-production + audience-fit)
- 한국어 보고형(~함/~임/~필요)으로 작성. 구조:
  - 제목 → 3줄 요약 → 본문 → (출처는 '명함/네트워크 기반'으로 표기)
  - 본문은 인물·회사별로 묶고, 각 항목에 **시사점 / 기회 / 후속 액션**을 1~2줄.
  - 전체를 관통하는 결론(이번 기간 네트워크가 호주 사업에 주는 함의)을 앞에 배치.
- **`facts.md` 를 진실로 간주**하고 충돌 시 facts 우선, 추정은 "가설/확인 필요"로 표기.
- 결과를 `runs/<run-id>/contacts-draft.md` 로 저장(run-id = `<날짜>_contacts_<난수>`).

## 3. 게시 (slack-post 재사용)
- `archive/<날짜>_contacts.md` 로 저장.
- 기본(승인 게이트): `.claude/skills/slack-post/scripts/send.py --file <draft> --stage draft --transport webapi --channel-alias staging --run-id <run-id> --target-alias <대상채널>`
- `--publish` 면: `send.py --stage final --transport webapi --channel-alias <대상채널>` 로 바로 게시.

## 4. 마무리
- 한 줄 결과: run-id, 포함 인물 수, 게시 위치/단계.

> 이 보고서는 웹 사실검증(reviewer) 대상이 아니므로(내부 메모 기반), 수치·외부 주장은 본문에서 "확인 필요"로 보수적으로 다룬다.
