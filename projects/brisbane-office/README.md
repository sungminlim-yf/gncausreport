# 스타터 팩 — 브리즈번 상업용 오피스 시장 보고 프로젝트

지엔씨에너지 **호주 투자팀**용. 이 폴더는 [`../../TEMPLATE.md`](../../TEMPLATE.md) 이식 가이드의 §2
"내용물" 3종을 이 주제에 맞게 미리 작성한 것이다. 새 레포로 옮길 때 루트로 복사해 쓴다.

```
facts.md                         → 새 레포 루트 facts.md
topics.md                        → 새 레포 루트 topics.md
audience/briefs/invest-team.md   → 새 레포 audience/briefs/invest-team.md
```

## 프로젝트 설정값

| 항목 | 값 |
|---|---|
| 주제 | 브리즈번 CBD 상업용 오피스 시장 동향·전망 |
| 독자(채널 별칭) | `invest-team` |
| 슬랙 채널 ID | `C0B9FUYU2RG` (youngfoods 워크스페이스) |
| 통화 | 호주달러(AUD) |

## .env 매핑 (새 레포)

```bash
# 채널 별칭 → 채널 ID
SLACK_CHANNEL_INVEST_TEAM=C0B9FUYU2RG
SLACK_CHANNEL_STAGING=        # 승인 대기 스테이징 채널 ID(별도 권장)
SLACK_CHANNEL_OPS=            # 운영 알림 채널 ID

# 메일(선택)
EMAIL_SUBJECT_PREFIX=[브리즈번 오피스 리포트] 
```

## 새 레포에서 추가로 해야 할 치환 (TEMPLATE.md §3)

- 기본 채널 별칭 `exec-team` → `invest-team`
  - `scripts/run_topics.sh`, `scripts/topics_tool.py`(`DEFAULT_CHANNEL`), `bot/server.py`(`target_alias`·QA 기본), `.claude/commands/brief.md`
- 슬래시 명령 `/지엔씨`·`/gnc` → 프로젝트에 맞는 이름(원하면 유지 가능)
- systemd/launchd 유닛명·경로(`gncausreport-*`, `~/gncausreport`) → 새 레포명
- `bot/server.py` `_QA_PROMPT_WEB` 독자 정의를 "호주 투자팀"으로
- `context.md`·`에이전틱_자동화_설계도.md` 배경 문서 갱신

## ⚠️ 거래 보안 (중요)

진행 중인 실거래라 `facts.md §0`에 **거래 가격·조건을 대외비로 명시**하고, 보고서는
**시장 일반 동향만** 다루도록 가드레일을 넣었다. reviewer가 거래 암시 표현을 `fail` 처리한다.
운영 시 이 가드레일을 약화시키지 말 것.

## 첫 실행 테스트

```bash
/brief "브리즈번 CBD 오피스 공실률·임대료 분기 동향" invest-team --depth medium
# → 스테이징 채널에 초안+승인버튼 게시 → 승인 → invest-team(C0B9FUYU2RG) 본 게시
```
