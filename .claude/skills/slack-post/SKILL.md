---
name: slack-post
description: 완성된 보고서를 슬랙 채널에 게시한다. 게시 전 archive에 저장하고 posted-index를 갱신한다. 2단계 이후엔 스테이징 채널 초안(+승인버튼)→승인→본 게시. 채널은 슬랙 Web API 일원화(1단계 한정 Incoming Webhook 스캐폴드). scripts/send.py 번들.
---

# slack-post — 슬랙 게시

완성본을 슬랙에 게시한다. **게시 전 항상 archive에 저장**해 봇 답변·중복방지의 근거로 남긴다.

## 절차

1. **archive 저장 (게시보다 먼저)**
   - 통과한 `runs/<run-id>/draft.md`를 `archive/<YYYY-MM-DD>_<주제>.md`로 복사한다.
   - `archive/posted-index.md`에 이 보고가 사용한 **모든 출처 URL·제목·게시일**을 추가한다(D6 중복 방지).

2. **전송** — `scripts/send.py` 호출
   ```bash
   python .claude/skills/slack-post/scripts/send.py \
     --file archive/<YYYY-MM-DD>_<주제>.md \
     --channel-alias <대상채널 또는 staging> \
     --run-id <run-id> \
     --stage draft|final
   ```
   - 본문 전체를 메시지로 전송하되, 슬랙 블록 길이 제한을 넘으면 **자동 분할 전송**한다(D10).

## 1단계 vs 2단계+ (D21)

- **1단계(현재 목표, D22)**: `--stage final --transport webhook`로 **테스트 채널에 바로** 게시(Incoming Webhook 스캐폴드). 승인·스레드 없음 — "채널 도달"만 증명.
- **2단계+**: `--stage draft --transport webapi`로 **스테이징 채널에 초안+승인버튼** 게시(`chat.postMessage`). 이후 지정 승인자가 [승인] 버튼 클릭(D5·D17) → 봇(`bot/server.py`)이 **대상 채널에 본 게시**.

> `send.py`는 전송 추상화(`--transport webhook|webapi`, 기본은 `.env`의 `SLACK_TRANSPORT`)로 설계되어 1단계 Webhook이 2단계에서 사장되지 않는다.

## Block Kit 승인 버튼 (D5)
- `--stage draft`이면 메시지에 **[✅ 승인] / [❌ 반려]** 버튼 블록을 부착한다.
- 버튼 `action_id`: `approve_post` / `reject_post`, `value`: `run-id`(또는 archive 경로). 봇이 이 값으로 어떤 초안을 본 게시할지 식별한다.

## 비밀정보 (환경변수)
- Webhook URL·Bot Token(`xoxb-`)·채널 ID는 **모두 `.env`**에서 읽는다. SKILL.md/코드에 하드코딩 금지.

## 실패 처리 (D19)
- 전송 실패(API 오류 등) 시 비영(non-zero) 종료하고 사유를 출력 → 오케스트레이터가 운영자 채널에 알림.
