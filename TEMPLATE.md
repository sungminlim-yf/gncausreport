# 이식 가이드 — 이 파이프라인을 다른 프로젝트/주제로 재사용하기

이 레포는 **"보고서 자동 생성·게시 엔진"** 과 **"GNC(지엔씨에너지) 전용 내용물"** 이 분리되어
있어, 다른 주제로 옮기기 쉽다. 이 문서는 **무엇을 그대로 두고, 무엇을 갈아끼우고, 코드 안의
어떤 고유명사를 치환하는지** 를 체크리스트로 정리한 것이다.

> 한 줄 요약: **코드(엔진)는 그대로**, **내용물 파일을 교체**, **하드코딩된 브랜드/경로 문자열만 치환**.

---

## 1. 그대로 가져가는 "엔진" (로직 수정 거의 없음)

아래는 주제와 무관한 범용 로직이다. 파일 내 GNC 고유명사(아래 §3)만 치환하면 된다.

| 경로 | 역할 |
|---|---|
| `.claude/agents/researcher.md` | 다매체 검색·수집 (출처 보존) |
| `.claude/agents/curator.md` | 후보 선별·점수화 |
| `.claude/agents/reviewer.md` | 팩트체크·인용 검수 게이트 |
| `.claude/skills/curate/` | 선별 루브릭 |
| `.claude/skills/content-production/` | 글쓰기·인용 양식 |
| `.claude/skills/audience-fit/` | 독자 맞춤 |
| `.claude/skills/slack-post/` (+ `scripts/send.py`) | 슬랙 게시 |
| `.claude/commands/brief.md` | 파이프라인 오케스트레이션 |
| `.claude/commands/refresh-brief.md` | 독자 카드 재간추림 |
| `bot/server.py`, `bot/email_sender.py`, `bot/slack-app-manifest.yaml` | 슬랙 봇(승인·Q&A)·메일 |
| `scripts/` (`run_topics.sh`, `run_refresh.sh`, `topics_tool.py`) | 스케줄 실행·주제 도구 |
| `deploy/` (systemd 유닛·`setup.sh`) | 상시 봇·타이머 배포 |
| `slack_blocks.py` | 슬랙 메시지 블록 포맷 |
| `runs/` | 실행 산출물 폴더(자동 생성) |

---

## 2. 갈아끼우는 "내용물" (← 새 프로젝트로 교체)

| 파일/폴더 | 조치 |
|---|---|
| **`facts.md`** | GNC 사실(디젤·점유율 등) 전부 삭제 → 새 프로젝트의 확정 사실로 교체. 비워두고 시작해도 됨 |
| **`topics.md`** | 데이터센터·발전기 주제 → 새 테마 주제로 교체 (5컬럼 스키마 유지: `주제 \| 채널 \| depth \| 요일 \| 상태`) |
| **`audience/briefs/exec-team.md`** | 새 독자 카드로 교체. 채널 별칭을 바꾸면 파일명도 함께 변경 |
| **`audience/profiles/`** | (있으면) 독자 원본 자료 교체 |
| **`sungmindata/`** | GNC 1차 사업자료 → 새 프로젝트 자료로 교체 |
| **`context.md`** | 프로젝트 배경·설계 의도 새로 작성 |
| **`에이전틱_자동화_설계도.md`** | SSOT 설계 문서 — 새 프로젝트 기준으로 갱신(또는 유지하되 브랜드만 치환) |
| **`.claude/skills/topic-curator/`**, `.claude/commands/refresh-topics.md` | GNC 사업 로직 참조 부분을 새 도메인 기준으로 |
| **`archive/`** | 과거 게시물·`posted-index.md`·`topics-history.md` 비우기(새 프로젝트는 이력 0부터) |
| **`.env`** (커밋 안 됨) | `.env.example` 보고 새 토큰·채널·수신자로 작성 |

---

## 3. 코드 안의 하드코딩된 고유명사·경로 치환 목록

엔진 코드에 박혀 있어 **반드시 찾아 바꿔야 하는** 항목. (검색어: `지엔씨`, `gnc`, `GNC`, `gncausreport`, `exec-team`)

### 3-1. 슬래시 명령 이름 (`/gnc`, `/지엔씨`)
- `bot/slack-app-manifest.yaml` — `/gnc`, `/지엔씨` 명령 정의 → 새 명령명으로
- `bot/server.py` — 한/영 명령 키워드 라우팅과 사용자 안내 문구(`/지엔씨 조사`·`주제`·`도움` 등) 전반

### 3-2. 채널 기본 별칭 (`exec-team`)
여러 곳에 기본값으로 박혀 있음. 새 기본 채널 별칭으로 통일:
- `scripts/run_topics.sh` (기본 channel)
- `scripts/topics_tool.py` (`DEFAULT_CHANNEL`, 통보 채널 순서)
- `bot/server.py` (`target_alias` 기본값, Q&A 기본 채널)
- `.claude/commands/brief.md` (대상채널 기본값)

### 3-3. systemd / launchd 유닛·라벨 (`gncausreport-*`, `com.gncausreport.*`)
- `deploy/gncausreport-bot.service`, `-brief.service`, `-brief.timer`, `-refresh.service`, `-refresh.timer` → 파일명·내용 새 프로젝트명으로
- `deploy/setup.sh`, `deploy/README.md` — 유닛명 참조
- `bot/server.py` — `systemctl show gncausreport-brief.timer ...` (다음 실행시각 조회) 유닛명
- `scripts/com.gncausreport.brief.plist` — launchd 라벨·경로
- `scripts/README.md` — plist 라벨 참조

### 3-4. 절대 경로 (`~/gncausreport`, `/Users/limsungmin/gncausreport`)
- `scripts/com.gncausreport.brief.plist` — `/Users/<나>/<레포>/...`, 로그 경로
- `deploy/` 유닛 파일들 — `WorkingDirectory`·`ExecStart` 경로
- 새 레포 경로로 일괄 치환

### 3-5. 브랜드 문자열·기타
- `bot/server.py` `_QA_PROMPT_WEB` — "지엔씨에너지(GNC Energy) 경영진" → 새 프로젝트 독자 정의
- `bot/server.py` 쿼터 파일 `~/.gncausreport-quota.json` → 새 이름
- `bot/email_sender.py` `User-Agent: gncausreport-bot/1.0` → 새 이름
- `.env.example` 주석의 예시 메일/제목 머리말(`EMAIL_FROM`, `EMAIL_SUBJECT_PREFIX`)

---

## 4. 단계별 절차 (권장: 템플릿 fork)

1. **새 레포 생성**: 이 레포를 복제 → `.git` 제거 후 새로 `git init` (또는 GitHub "Use this template")
2. **내용물 비우기/교체** (§2): `facts.md`·`topics.md`·`audience/`·`sungmindata/`·`context.md`·`archive/`
3. **고유명사 치환** (§3): 위 목록대로 `지엔씨`/`gnc`/`gncausreport`/`exec-team`/경로를 새 프로젝트 값으로
   - 일괄 치환 시작점:
     ```bash
     grep -rIl --exclude-dir=.git -e '지엔씨' -e 'gncausreport' -e 'exec-team' .
     ```
4. **새 슬랙 앱** 생성 → `bot/slack-app-manifest.yaml`(명령명 수정본)로 앱 import → 토큰 발급
5. **`.env` 작성**: `.env.example` 복사 → 새 `SLACK_BOT_TOKEN`/`SLACK_APP_TOKEN`/채널 ID/`SLACK_APPROVERS`/메일 설정
6. **배포 유닛 설치**: `deploy/setup.sh`(유닛명·경로 수정본) 실행, 봇 상시 가동 확인
7. **테스트**: 한 주제로 `/brief <주제> <채널>` → 스테이징 채널에 초안+승인버튼 게시 확인 → 승인 → 본 게시·메일 확인

---

## 5. 이식 체크리스트

- [ ] `facts.md` — 새 프로젝트 사실로 교체(또는 비움)
- [ ] `topics.md` — 새 주제표(5컬럼 스키마 유지)
- [ ] `audience/briefs/` — 새 독자 카드(채널 별칭 일치)
- [ ] `sungmindata/`·`context.md`·`에이전틱_자동화_설계도.md` — 새 배경 자료
- [ ] `archive/` 비움(`posted-index.md`·`topics-history.md` 초기화)
- [ ] 슬래시 명령명 치환(manifest + server.py)
- [ ] 채널 기본 별칭 `exec-team` 치환
- [ ] systemd/launchd 유닛명·라벨·경로 치환
- [ ] `_QA_PROMPT_WEB` 독자 정의·쿼터 파일명·User-Agent 치환
- [ ] 새 슬랙 앱·`.env` 작성
- [ ] `/brief` 1건 엔드투엔드 테스트 통과

---

> 참고: 엔진(서브에이전트·스킬·봇)의 **로직 자체는 주제 불문 재사용**된다. 품질은 결국
> `facts.md`(오해 차단)·`audience/briefs`(독자 적합성)·`topics.md`(주제 설계) 세 내용물 파일의
> 정성에서 갈린다 — 이식 후 이 셋을 채우는 데 시간을 쓰는 것이 가장 효과적이다.
