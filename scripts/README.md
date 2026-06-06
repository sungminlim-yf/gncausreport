# 정기 실행 (스케줄) 런북 — 월·수·금 08:00 (D14)

`topics.md`의 주제들을 정한 시각에 자동으로 `/brief` 실행해 **스테이징 채널에 초안+승인버튼**을 올린다.
본 게시는 사람이 [승인]을 눌러야 진행된다(D2) — **무인 본게시는 일어나지 않는다.**

## 구성 요소
- `scripts/run_topics.sh` — topics.md를 읽어 각 주제로 `claude -p "/brief …"`를 헤드리스 실행.
- `scripts/com.gncausreport.brief.plist` — launchd 잡 정의(월=1·수=3·금=5, 08:00). 실제 설치본은 `~/Library/LaunchAgents/`.

## 트리거·주기
- **트리거**: macOS **launchd**(StartCalendarInterval). 월·수·금 08:00 자동 실행.
- **간격 바꾸기**: plist의 `StartCalendarInterval` 수정(`Weekday` 0=일~6=토, `Hour`, `Minute`) 후 재설치(아래).
- **무엇을 실행하나**: `topics.md`의 `<주제> | <채널> | <depth>` 줄들. 줄을 `#`로 주석 처리하면 그 주제는 건너뛴다(실행량 조절).

## 설치 / 해제 / 확인
```bash
PLIST="$HOME/Library/LaunchAgents/com.gncausreport.brief.plist"
# (repo 사본을 설치본으로 복사하려면)
cp "scripts/com.gncausreport.brief.plist" "$PLIST"

launchctl unload "$PLIST" 2>/dev/null   # 해제
launchctl load -w "$PLIST"              # 설치(활성)
launchctl list | grep gncausreport      # 등록 확인
```

## 수동 테스트
```bash
# 1) 파싱만 확인(실제 실행 안 함)
DRY_RUN=1 bash scripts/run_topics.sh

# 2) 실제 1회 실행(모든 주제 → 각 스테이징 초안 게시, 토큰 소모)
bash scripts/run_topics.sh
#    특정 모델로:  MODEL=claude-sonnet-4-6 bash scripts/run_topics.sh

# 3) launchd로 즉시 1회 발사(스케줄과 동일 경로)
launchctl start com.gncausreport.brief
```
로그: `logs/brief_<타임스탬프>.log`, launchd 표준출력: `logs/launchd.out.log` / `logs/launchd.err.log`.

## 전제 조건
- 08:00에 **맥이 깨어 있고 사용자 로그인 상태**여야 한다(LaunchAgent). 자는 동안 놓친 잡은 깨어날 때 1회 실행된다.
- `claude` CLI가 **헤드리스로 인증**돼 있어야 한다(로그인 상태 유지).
- 승인 버튼 처리를 위해 **봇(`bot/server.py`)이 떠 있어야** 본 게시가 가능하다(초안 생성 자체는 봇 없이도 됨).
- `--dangerously-skip-permissions`로 무인 실행한다 → repo 디렉터리에서 전체 도구 권한으로 동작. 신뢰된 로컬 자동화에 한해 사용.

## 실행량(중요)
주제 N개 × 주 3회 = **주당 3N건**의 초안. 현재 topics.md 6개 → 주 18건.
부담되면 `topics.md`에서 줄을 `#` 주석 처리해 줄인다(예: 핵심 2~3개만 활성).
