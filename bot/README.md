# 슬랙 봇 런북 (2단계 승인 + 3단계 Q&A, Socket Mode)

`bot/server.py`는 **승인 버튼 수신(2단계)**과 **스레드 Q&A(3단계)**를 담당하는 상시 가동 프로세스다.
Socket Mode(아웃바운드 WebSocket)라 **공개 HTTPS·고정 도메인·인증서가 필요 없다**(D13). 로컬·NAT 뒤에서도 가동된다.

> 2단계에서 "스테이징 초안 → 승인 버튼 → 본 게시"를 쓰려면 이 봇이 **떠 있어야** 버튼이 동작한다.

---

## 1. 슬랙 앱 설정 (1단계에서 만든 앱 재사용 가능)

https://api.slack.com/apps → 해당 앱 선택.

### a) Socket Mode 켜기 → App Token(`xapp-`) 발급
- **Settings → Socket Mode** → Enable 토글 On
- App-Level Token 생성: 스코프 **`connections:write`** → 토큰(`xapp-...`) 복사 → `.env`의 `SLACK_APP_TOKEN`

### b) Bot Token(`xoxb-`) 스코프 부여
- **Features → OAuth & Permissions → Bot Token Scopes**에 추가(최소 권한, §11):
  - `chat:write` — 게시·회신(필수)
  - `channels:history`, `groups:history` — 스레드 질문 수신(3단계 Q&A)
  - (선택) `chat:write.public` — 봇이 미가입 공개 채널에도 게시
- **Install App to Workspace**(또는 Reinstall) → Bot User OAuth Token(`xoxb-...`) 복사 → `.env`의 `SLACK_BOT_TOKEN`

### c) 이벤트 구독 (3단계 Q&A를 켤 때만)
- **Features → Event Subscriptions** → Enable. Socket Mode면 URL 불필요.
- Subscribe to bot events: `app_mention`, `message.channels`, `message.groups`
- 2단계(승인만)에서는 버튼 액션만 쓰므로 이벤트 구독 없이도 동작한다.

### d) 봇을 채널에 초대
- 스테이징·대상(exec-team)·운영(ops) 채널에서 `/invite @봇이름` (chat:write.public 없으면 필수)

---

## 2. `.env` 채우기

```
SLACK_TRANSPORT=webapi              # 2단계는 webapi
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL_EXEC_TEAM=C...        # 대상(본 게시) 채널 ID
SLACK_CHANNEL_STAGING=C...          # 스테이징(초안+승인버튼) 채널 ID
SLACK_CHANNEL_OPS=C...              # 운영자 알림 채널 ID (선택)
SLACK_APPROVERS=U...,U...           # 승인 가능한 슬랙 user ID(쉼표 구분, D17)
ARCHIVE_ACTIVE_MONTHS=6
QA_ENABLED=                         # 3단계 켤 때 1/true (2단계는 비워둠 → Q&A 침묵)
```

> 채널 ID(C…)는 슬랙에서 채널 우클릭 → "채널 세부정보 보기" 하단, 또는 채널 URL 끝 토막.
> 본인 user ID(U…)는 프로필 → 더보기(⋯) → "멤버 ID 복사".

---

## 3. 설치 · 실행

```bash
python3 -m venv .venv
.venv/bin/pip install -r bot/requirements.txt
.venv/bin/python bot/server.py        # "Socket Mode 연결 시작" 출력되면 가동 중
```

상시 가동(데모 후에도 유지)하려면 `nohup .venv/bin/python bot/server.py &` 또는 launchd/systemd/tmux 사용.

---

## 4. 2단계 승인 종단 테스트

1. 봇을 띄운다(위 3).
2. 보고서를 스테이징에 초안 게시:
   ```bash
   .venv/bin/python .claude/skills/slack-post/scripts/send.py \
     --file archive/2026-06-06_호주-건기식-규제변화.md \
     --channel-alias staging --stage draft --transport webapi \
     --run-id 2026-06-06_au-supp-reg_demo1 --target-alias exec-team
   ```
3. 스테이징 채널의 초안 메시지에서 **[✅ 승인]** 클릭(승인자 화이트리스트 사용자로).
4. 봇이 **exec-team 채널에 본 게시**하고 초안 메시지를 "승인됨"으로 갱신하면 성공.
   - 화이트리스트 외 사용자가 누르면 "권한 없음" 에페메럴 안내 후 무시(D17).

---

## 5. 3단계 Q&A 종단 테스트

질문은 두 경로로 받는다: **(1) 게시된 글 스레드**(그 글만 근거, 정밀, D9) / **(2) 채널 직접 질문**(활성 보고서 전체에서 관련 글 탐색 — 슬랙 미숙 사용자 배려).

### a) 켜기
1. 슬랙 앱에서 **Event Subscriptions** 활성 + 봇 이벤트 `app_mention`·`message.channels`·`message.groups` 구독(위 1-c), **Reinstall**.
2. `.env`에서 `QA_ENABLED=1`, `QA_MODEL=claude-haiku-4-5`(채널 질문은 다중 보고서라 가벼운 모델 권장) → **봇 재시작**.

### b-1) 글 스레드 질문 (정밀)
1. 승인 플로우(위 4)로 exec-team 채널에 본 게시된 글을 연다.
2. 그 메시지 **스레드에** 질문(예: "비타민 B6 채널 제한은 언제 발효되나요?"). 봇 멘션은 선택.
3. 봇이 `💬 …작성하는 중…` 표시 후, **그 글 한 건**만 근거로 출처 포함 답변으로 갱신.

### b-2) 채널 직접 질문 (스레드 불필요)
1. exec-team 채널(또는 `QA_CHANNELS`)에 **그냥 질문을 친다**(예: "호주 발전기 시장 경쟁사 점유율은?").
2. 질문 신호(물음표·의문 키워드)가 있으면, 봇이 **활성 보고서들 중 관련 글을 골라** 답하고, 답변을 **스레드+채널 동시 노출**(reply_broadcast)해 누구나 바로 본다.
3. 어느 보고서로도 답할 수 없으면 "게시된 보고서로는 답하기 어렵습니다"라고 정직하게 밝힌다.

### c) 동작 원리
- 그라운딩: 스레드가 특정 글에 바인딩(`approved.ts == thread_ts`, D9)되면 **그 글만**, 아니면(채널 질문) `active_archives()`의 **활성 보고서 전체**(최신 ≤10건)에서 관련 글을 LLM이 선택.
- 생성: `_generate_answer()`가 헤드리스 `claude -p`로 보고서 전문만 근거로 답변(도구 미사용·추측 금지·출처 표기). 스케줄러·`/gnc 조사`와 동일 런타임 → 새 의존성·API 키 불필요.
  - **QA_MODEL**: 채널 질문은 여러 보고서를 한 번에 넣으므로 **가벼운 모델**(예: `claude-haiku-4-5`, ~10초/건) 권장 — Opus는 다중 보고서에서 타임아웃 위험.
- 채널 게이트: 채널 직접 질문은 `QA_CHANNELS`(미설정 시 exec-team) + 질문 신호일 때만. 스레드 질문은 채널과 무관하게 동작.
- 비차단(D4): 이벤트는 Bolt가 자동 ack, 답변은 백그라운드 스레드. `app_mention`+`message` 중복 발화는 `(channel,ts)` 1회 처리로 제거.
- 보관 한도(D24): `ARCHIVE_ACTIVE_MONTHS` 경과 글은 그라운딩 후보에서 제외.

---

## 슬랙에서 직접 제어 — `/gnc` 슬래시 명령 (모바일 OK)

봇이 떠 있으면 슬랙(모바일 포함)에서 조사 트리거·주제 관리를 직접 할 수 있다. 지정 승인자(D17)만 사용 가능.

```
/gnc brief <주제>                      지금 바로 조사 → 스테이징 초안+승인버튼
/gnc topics                            정기 주제 목록(월·수·금 08:00 실행 대상)
/gnc topic add <주제> | <채널> | <depth>   주제 추가(채널·depth 생략 시 exec-team/medium)
/gnc topic rm <번호>                   주제 삭제
/gnc help                              도움말
```

### 슬래시 명령 등록 (1회, 앱 설정)
1. https://api.slack.com/apps → 앱 → **Features → Slash Commands → Create New Command**
2. Command: `/gnc` · Short Description: `GNC 리포트 봇` · Usage Hint: `brief <주제> | topics | topic add … | topic rm <n>`
3. **Socket Mode가 켜져 있으면 Request URL은 비워도 된다**(소켓으로 전달). Save.
4. `commands` 스코프가 추가되며 **앱 재설치(Reinstall)** 안내가 뜨면 진행(토큰 유지).

> 트리거로 만든 초안도 **사람 승인(D2)**을 거쳐야 본 게시된다. 즉시 조사는 헤드리스 `claude -p`를
> 백그라운드로 실행하므로(스케줄러와 동일 경로) 완료까지 수 분 걸리고, 끝나면 봇이 회신한다.

## 동작 요약 (코드 ↔ 결정)

| 기능 | 코드 | 결정 |
| --- | --- | --- |
| 즉시 ack | 핸들러 첫 줄 `ack()` | D4 |
| 승인 인가 | `is_approver()` + 화이트리스트 | D17 |
| run_id↔archive 조회 | `find_archive_for_run()` ← `archive/run-index.json` | D9 |
| 승인 시 본 게시 + 스레드 기록 | `handle_approve()` → `approved{ts}` 저장 | D2·D9 |
| Q&A 스레드 바인딩 | `find_archive_for_thread()` (approved.ts 매칭) | D9 |
| Q&A 답변 생성 | `_answer_question()`→`_qa_work()`→헤드리스 `claude -p` (QA_ENABLED 게이트) | 3단계 |
| 활성 보관 한도 | `ARCHIVE_ACTIVE_MONTHS` 컷오프 | D24 |
| 운영자 알림 | `_notify_ops()` | D19 |
