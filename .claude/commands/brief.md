---
description: 주제·대상채널을 받아 researcher→curator→writer→reviewer→게시 파이프라인을 명시적으로 오케스트레이션한다.
argument-hint: <주제> <대상채널> [--depth shallow|medium|deep] [--budget <토큰상한>] [--publish]
---

# /brief — 보고서 생성·게시 오케스트레이션

입력: `$ARGUMENTS` (형식: `<주제> <대상채널> [--depth ...] [--budget ...]`)

각 단계를 **명시적으로** 호출한다(자동 위임은 불완전하므로). 모든 단계 입출력은 `runs/<run-id>/`에 파일로 남긴다(D1).

## 0. 준비
1. 인자를 파싱: 주제, 대상채널(기본 `exec-team`, D20), `--depth`(기본 `medium`, D8), `--budget`(D16), `--publish`(있으면 자동게시 모드 — 5단계 참고. 슬랙 `/지엔씨 조사`가 멤버 트리거 시 부여. 정기 스케줄러는 부여하지 않음).
2. **run-id 생성**: `<YYYY-MM-DD>_<주제슬러그>_<짧은난수>`. `runs/<run-id>/` 폴더 생성.
3. `runs/<run-id>/meta.json`에 주제·대상채널·depth·budget·시작시각 기록.
4. 예산 상한이 있으면 이후 단계에서 토큰/검색 호출을 추적해 초과 시 중단·경고(D16, `cost.json`).

## 1. researcher (서브에이전트)
- `researcher` 서브에이전트를 호출: 주제 + `runs/<run-id>/` 경로 전달.
- 결과 `runs/<run-id>/researcher.json` 생성 확인. **0건이면 중단하고 운영자 알림**(D19).

## 2. curator (서브에이전트 + curate 스킬)
- `curator` 서브에이전트 호출: `researcher.json` → `curator.json`(상위 N건).
- 통과 0건이면 중단·알림(D19).

## 3. writer (메인 + content-production + audience-fit)
- 메인 세션이 `content-production`·`audience-fit` 스킬로 `curator.json` 통과 건과 `audience/briefs/<대상채널>.md`를 읽어 **한국어 보고서**를 작성.
- 결과 `runs/<run-id>/draft.md` (제목→3줄요약→본문→📎출처, 핵심 수치 원문 병기 D3, `[번호]` 인용).

## 4. reviewer (서브에이전트) — 품질 게이트 + 루프(D7)
- `reviewer` 서브에이전트 호출: `draft.md` 핵심 수치·고위험 주장만 원문 대조(D18) → `review.json`.
- `verdict == fail`이면 **3번(writer)으로 회귀**해 수정 후 재검수.
- **최대 2~3회**. 그 후에도 `fail`이면 중단하고 미해결 이슈를 표시해 **사람에게 에스컬레이션**(스테이징 채널/운영자 알림).

## 5. slack-post (스킬) — 게시
- `pass`한 `draft.md`를 `archive/<날짜>_<주제>.md`로 저장하고 `posted-index.md` 갱신(D6).
- 게시 방식은 `--publish` 유무로 분기한다:
  - **기본(승인 게이트, 현재 표준)**: `send.py --stage draft --transport webapi --channel-alias staging --run-id <run-id> --target-alias <대상채널>`로 **스테이징 채널 초안+승인버튼** 게시. send.py가 `archive/run-index.json`에 매핑을 남기고, **상시 가동 봇(`bot/server.py`)**이 [승인] 클릭을 수신해 `--target-alias` 채널에 본 게시한다(D2·D5). 정기 스케줄러·일반 호출은 이 경로.
  - **`--publish` 자동게시(D2 예외 — 슬랙 `/지엔씨 조사` 멤버 트리거 전용)**: **4단계 reviewer가 `pass`인 경우에만** `send.py --stage final --transport webapi --channel-alias <대상채널>`로 **승인 없이 대상 채널에 바로 본 게시**한다(승인버튼·run-index 불필요).
    - ⚠️ **검수 안전장치**: reviewer가 끝내 `fail`이면(2~3회 재시도 후에도) **자동게시하지 말 것**. 대신 기본 경로처럼 `--stage draft`로 **스테이징에 보류**(+미해결 이슈 표기)해 사람이 검토하도록 에스컬레이션한다. 자동게시라도 검수 미통과 글은 외부 채널로 내보내지 않는다.
  - (참고 — 1단계 스캐폴드 `send.py --stage final --transport webhook`은 폐기 경로.)

## 6. 마무리
- `runs/<run-id>/cost.json`에 비용·호출 수 기록, 예산 대비 보고.
- 한 줄 결과 요약 출력: run-id, 통과 건수, 게시 위치/단계, 미해결 이슈 유무.

> 어떤 단계든 경갑한 실패(검색 0건·API 장애·예산 초과·승인 타임아웃)는 **운영자 채널 알림**(D19)으로 통보한다.
