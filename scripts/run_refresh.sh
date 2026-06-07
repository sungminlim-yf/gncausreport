#!/usr/bin/env bash
# 토요일 자동 주제 갱신 래퍼 — 다음 주 9개 주제를 자동 선정·확정하고 슬랙 통보.
# systemd 타이머(토 08:00 시드니)가 호출한다. /refresh-topics --weekly --auto 가
# topic-curator 로 9개를 도출 → topics_tool apply-weekly 로 전체 교체(3·3·3) → notify 통보.
# 보고서 게시 승인 게이트(D2)는 그대로이므로, '주제 선정'만 무인 자동이다.
#
# 환경변수:
#   DRY_RUN=1  실제 claude 호출 없이 실행할 명령만 로그에 출력(테스트용).
#   MODEL=...  /refresh-topics 에 쓸 모델(미지정 시 claude 기본).
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
CLAUDE="${CLAUDE_BIN:-$(command -v claude || true)}"
[ -n "$CLAUDE" ] || { echo "claude CLI 를 찾을 수 없음 (CLAUDE_BIN 설정 또는 PATH 확인)"; exit 1; }

cd "$REPO" || { echo "repo 경로 없음: $REPO"; exit 1; }

# .env 로드(있으면) — ANTHROPIC_API_KEY(=claude), SLACK_BOT_TOKEN(=통보) 등을 서브프로세스에 전달.
if [ -f "$REPO/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$REPO/.env"
  set +a
fi
mkdir -p "$REPO/logs"
STAMP="$(date +%Y-%m-%d_%H%M%S)"
LOG="$REPO/logs/refresh_${STAMP}.log"
DRY="${DRY_RUN:-0}"
MODEL_ARG=()
[ -n "${MODEL:-}" ] && MODEL_ARG=(--model "$MODEL")

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

PROMPT="/refresh-topics --weekly --auto"
log "토요일 자동 주제 갱신 시작 (DRY_RUN=$DRY): $PROMPT"
if [ "$DRY" = "1" ]; then
  log "(DRY_RUN — 실제 실행 생략)"
  exit 0
fi
if "$CLAUDE" -p "$PROMPT" "${MODEL_ARG[@]}" --dangerously-skip-permissions >>"$LOG" 2>&1; then
  log "✅ 주제 갱신 완료 — 다음 주 월·수·금 정기 실행에 반영. 로그: $LOG"
else
  log "✗ 주제 갱신 실패 (로그 확인): $LOG"
  exit 1
fi
