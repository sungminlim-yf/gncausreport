#!/usr/bin/env bash
# 정기 실행 래퍼 (D14) — topics.md 의 각 주제로 /brief 를 헤드리스 실행.
# launchd(월·수·금 08:00)가 호출한다. 각 주제는 스테이징 채널에 "초안+승인버튼"으로 게시되며,
# 본 게시는 사람이 [승인] 버튼을 눌러야 진행된다(D2) — 즉 무인 본게시는 일어나지 않는다.
#
# 환경변수:
#   DRY_RUN=1  실제 claude 호출 없이 실행할 명령만 로그에 출력(테스트용).
#   MODEL=...  /brief 에 쓸 모델(미지정 시 claude 기본).
set -uo pipefail

REPO="/Users/limsungmin/Library/CloudStorage/Dropbox/96. dropbox_vsstudio/gncausreport"
CLAUDE="/Users/limsungmin/.local/bin/claude"
export PATH="/Users/limsungmin/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"

cd "$REPO" || { echo "repo 경로 없음: $REPO"; exit 1; }
mkdir -p "$REPO/logs"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG="$REPO/logs/brief_${STAMP}.log"
DRY="${DRY_RUN:-0}"
MODEL_ARG=()
[ -n "${MODEL:-}" ] && MODEL_ARG=(--model "$MODEL")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

log "정기 실행 시작 (DRY_RUN=$DRY)"
count=0
# topics.md 에서 비주석 + '|' 포함 줄만 주제로 파싱: '<주제> | <채널> | <depth>'
while IFS='|' read -r topic channel depth _rest; do
  topic="$(echo "${topic}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  channel="$(echo "${channel}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  depth="$(echo "${depth}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [ -z "$topic" ] && continue
  [ -z "$channel" ] && channel="exec-team"
  [ -z "$depth" ] && depth="medium"
  count=$((count + 1))
  PROMPT="/brief ${topic} ${channel} --depth ${depth}"
  log "▶ (${count}) ${PROMPT}"
  if [ "$DRY" = "1" ]; then
    log "   (DRY_RUN — 실제 실행 생략)"
    continue
  fi
  if "$CLAUDE" -p "$PROMPT" "${MODEL_ARG[@]}" --dangerously-skip-permissions >>"$LOG" 2>&1; then
    log "   ✓ 완료: ${topic}"
  else
    log "   ✗ 실패: ${topic} (로그 확인)"
  fi
  sleep 5
done < <(grep -vE '^[[:space:]]*[#>]' "$REPO/topics.md" | grep '|')

log "정기 실행 종료 — 주제 ${count}건 처리. 로그: $LOG"
log "다음: 스테이징 채널의 초안을 봇이 떠 있는 상태에서 [승인]하면 본 게시(D2)."
