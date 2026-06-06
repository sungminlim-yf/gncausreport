---
description: 주제·대상채널을 받아 researcher→curator→writer→reviewer→게시 파이프라인을 명시적으로 오케스트레이션한다.
argument-hint: <주제> <대상채널> [--depth shallow|medium|deep] [--budget <토큰상한>]
---

# /brief — 보고서 생성·게시 오케스트레이션

입력: `$ARGUMENTS` (형식: `<주제> <대상채널> [--depth ...] [--budget ...]`)

각 단계를 **명시적으로** 호출한다(자동 위임은 불완전하므로). 모든 단계 입출력은 `runs/<run-id>/`에 파일로 남긴다(D1).

## 0. 준비
1. 인자를 파싱: 주제, 대상채널(기본 `exec-team`, D20), `--depth`(기본 `medium`, D8), `--budget`(D16).
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
- 게시:
  - **1단계(현재, D22)**: `send.py --stage final --transport webhook --channel-alias <대상채널>`로 **테스트 채널 바로 게시**(승인·Q&A 생략).
  - **2단계+**: `send.py --stage draft --transport webapi --channel-alias staging --run-id <run-id>`로 **스테이징 채널 초안+승인버튼** → 봇이 [승인] 클릭 수신 후 본 게시 처리(D2·D5·§4.5).

## 6. 마무리
- `runs/<run-id>/cost.json`에 비용·호출 수 기록, 예산 대비 보고.
- 한 줄 결과 요약 출력: run-id, 통과 건수, 게시 위치/단계, 미해결 이슈 유무.

> 어떤 단계든 경갑한 실패(검색 0건·API 장애·예산 초과·승인 타임아웃)는 **운영자 채널 알림**(D19)으로 통보한다.
