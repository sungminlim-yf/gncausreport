# 클라우드 VM 배포 (Oracle Cloud 무료티어 + 종량제 API키)

맥북을 꺼도 작동하도록 **상시 봇 + 정기 스케줄러**를 Oracle Cloud 무료 VM 한 대에 올리는 절차.

## 왜 VM 한 대인가
- **봇**(`bot/server.py`, Socket Mode)은 슬랙 버튼 클릭·스레드 Q&A를 받으려고 **24시간 떠 있어야** 한다.
- **정기 조사 스케줄러**(`scripts/run_topics.sh`)는 월·수·금 그날 요일에 배정된 **주제 3건**을 `claude -p "/brief ..."`로 초안 게시한다.
- **주제 갱신 스케줄러**(`scripts/run_refresh.sh`)는 토요일에 `/refresh-topics --weekly --auto`로 **다음 주 9개 주제를 자동 선정**(Mon·Wed·Fri 3건씩)하고 슬랙 통보한다.
- 둘은 `archive/run-index.json`(초안↔본게시 바인딩 SSOT)을 **같은 파일시스템으로 공유**한다.
  → 쪼개면 상태 동기화가 깨지므로 **한 VM에 함께** 둔다. (GitHub Actions가 봇을 못 올리는 이유이기도 하다.)
- Socket Mode는 **아웃바운드 WebSocket**만 쓴다 → 공개 IP·포트개방·도메인 **불필요**.

## 목표 구성
| 구성요소 | 실행 방식 (Mac → 서버) |
|---|---|
| 봇 (상시) | launchd 수동기동 → **systemd** `gncausreport-bot.service` (Restart=always) |
| 정기 조사 (주3회) | **systemd timer** `gncausreport-brief.timer` (월·수·금 08:00, 그날 요일 3건) |
| 주제 갱신 (주1회) | **systemd timer** `gncausreport-refresh.timer` (토 08:00, 다음 주 9건 자동 선정) |
| Claude 인증 | 구독 로그인 → **종량제 `ANTHROPIC_API_KEY`** (Anthropic Console) |
| firecrawl MCP | `~/.claude.json`(전역) → 서버에서 `claude mcp add` 재등록 |

---

## 사전 준비
1. **Anthropic API 키** — console.anthropic.com → API Keys 발급(`sk-ant-...`). 종량제(토큰당) 과금.
2. **GitHub 접근** — private 레포 clone용 PAT(토큰) 또는 SSH 배포키. (`gh auth login`도 가능)
3. 맥의 **`.env` 파일** — 서버로 복사할 것(비밀값, git에 없음).

---

## 1단계 — Oracle Cloud 무료 인스턴스 생성
1. cloud.oracle.com 가입(무료티어, 영구 무료 한도 포함). **리전은 호주(Sydney 또는 Melbourne)** 선택.
2. **Compute → Instances → Create Instance**
   - 이미지: **Ubuntu 22.04 / 24.04 (aarch64)**
   - Shape: **VM.Standard.A1.Flex (Ampere, arm64)** — 무료 한도 내에서 **2 OCPU / 12 GB RAM** 권장
     (claude CLI는 arm64 빌드라 Ampere와 일치. Sydney에 A1 용량이 없으면 Melbourne 시도.)
   - SSH 키: 본인 공개키 등록(없으면 `ssh-keygen`으로 생성).
   - 네트워킹: 기본값 그대로. **인바운드 포트 열 필요 없음**(Socket Mode는 아웃바운드만).
3. 생성 후 **퍼블릭 IP** 확인.

## 2단계 — 접속 & 레포 clone
```bash
ssh ubuntu@<퍼블릭IP>

# private 레포 clone (PAT 사용 예)
git clone https://<GITHUB_USER>:<PAT>@github.com/sungminlim-yf/gncausreport.git ~/gncausreport
```

## 3단계 — `.env` 배치 (비밀값, git에 없음)
맥에서 한 줄로 복사:
```bash
# (맥 로컬 터미널에서)
scp ".env" ubuntu@<퍼블릭IP>:~/gncausreport/.env
```
그다음 서버에서 **API 키 추가**(맥 .env엔 비어 있음):
```bash
nano ~/gncausreport/.env     # ANTHROPIC_API_KEY=sk-ant-... 채우기
```
> `sungmindata/`(1차 사업자료)는 **토요일 자동 주제 갱신(`/refresh-topics --weekly --auto`)에 필요**하므로 서버에 scp 해 둔다. (없으면 주제 갱신이 중단됨)
> 회사 사실/가정은 `facts.md`(git 추적, 운영자 직접 편집)로 관리 — writer·reviewer가 읽어 오해를 차단.

## 4단계 — 부트스트랩 (멱등)
```bash
bash ~/gncausreport/deploy/setup.sh
```
이 스크립트가 하는 일: 시스템 패키지 → 타임존(Australia/Sydney) → **claude CLI 설치** → Python venv + 봇 의존성 → **firecrawl MCP 등록** → **systemd 봇/타이머 설치·기동**.

## 5단계 — 검증
```bash
claude -p "say hi"                              # ① API키 인증 OK?
systemctl status gncausreport-bot --no-pager    # ② 봇 active(running)?
systemctl list-timers 'gncausreport-*'          # ③ 정기 조사·주제 갱신 다음 실행 시각
journalctl -u gncausreport-bot -f               # ④ 실시간 로그(슬랙에서 /gnc 테스트)
```
- 슬랙에서 **`/gnc`** 또는 **`/지엔씨`** 명령 → 봇 응답 확인.
- 정기 조사 드라이런: `DRY_RUN=1 bash ~/gncausreport/scripts/run_topics.sh` (그날 요일 슬롯만)
- 주제 갱신 드라이런: `DRY_RUN=1 bash ~/gncausreport/scripts/run_refresh.sh`
- 실제 1회 강제 실행: `sudo systemctl start gncausreport-brief.service`(조사) / `sudo systemctl start gncausreport-refresh.service`(주제 갱신).

## 6단계 — 맥의 옛 실행 중단 (이중 게시 방지)
서버가 정상 확인되면 **맥에서 끈다**:
```bash
# (맥 로컬)
launchctl unload ~/Library/LaunchAgents/com.gncausreport.brief.plist 2>/dev/null
# 맥에서 봇을 수동 실행 중이었다면 그 프로세스도 종료.
```
> 같은 슬랙 앱 토큰으로 봇을 두 곳에서 켜면 이벤트가 중복 처리된다. **한 곳만** 켤 것.

---

## 운영 메모
- **업데이트 배포**: `cd ~/gncausreport && git pull && ./.venv/bin/pip install -r bot/requirements.txt && sudo systemctl restart gncausreport-bot`
- **봇 재시작**: `sudo systemctl restart gncausreport-bot`
- **로그**: 봇 `journalctl -u gncausreport-bot`, 정기 조사 `~/gncausreport/logs/brief_*.log`, 주제 갱신 `~/gncausreport/logs/refresh_*.log`
- **조사 요일 변경**: `gncausreport-brief.timer`의 `OnCalendar=` 줄을 가감 → `sudo systemctl daemon-reload`. (단, 주제 요일 배정은 Mon·Wed·Fri 고정이므로 다른 요일을 추가해도 그날 슬롯이 비어 조사 0건)
- **주제 갱신 시각 변경**: `gncausreport-refresh.timer`의 `OnCalendar=Sat ...` 수정.
- **주제 운영**: 토요일 자동 9건 선정. 주중 보충은 슬랙 `/지엔씨 주제갱신`(아직 조사 안 된 슬롯만 교체, 이미 조사된 건 유지). 추가/삭제 명령은 폐지됨.
- **비용 모니터링**: 종량제이므로 Anthropic Console의 Usage에서 토큰 소비 확인. 주당 9건(월·수·금 3건) × `BRIEF_DEFAULT_BUDGET`로 상한 관리(.env).
- **firecrawl 확인**: `claude mcp list` → `firecrawl` 보이면 OK.

## 알아둘 점
- **과금 전환**: 서버는 구독이 아니라 **API 종량제**다. 맥(구독)과 별개로 토큰당 비용 발생.
- **무인 본게시 없음**: 스케줄러는 초안+승인버튼만 게시(D2). 사람이 [승인]을 눌러야 본 게시 → 서버에서도 동일.
- **무료티어 유지**: Oracle 무료 인스턴스는 장기 미사용 시 회수될 수 있다. 상시 봇이 떠 있으면 사용 중으로 간주되어 안전한 편.
