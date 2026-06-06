#!/usr/bin/env bash
# Oracle Cloud(무료티어 Ubuntu, arm64) 부트스트랩 — 멱등(여러 번 실행해도 안전).
# 사용법:  bash ~/gncausreport/deploy/setup.sh
# 전제:    레포가 ~/gncausreport 에 clone 되어 있고, .env 가 채워져 있을 것(특히 ANTHROPIC_API_KEY).
set -euo pipefail

REPO="${REPO:-$HOME/gncausreport}"
[ -d "$REPO" ] || { echo "레포 없음: $REPO (먼저 git clone)"; exit 1; }

echo "[1/6] 시스템 패키지 설치"
sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git curl tzdata

echo "[2/6] 타임존 = Australia/Sydney (타이머가 시스템 타임존 기준)"
sudo timedatectl set-timezone Australia/Sydney || echo "  (타임존 설정 실패 — 무시하고 계속)"

echo "[3/6] claude CLI 설치 (~/.local/bin)"
if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
  # 공식 네이티브 설치 스크립트(arm64 자동 감지). 실패 시 npm 폴백:
  #   sudo apt-get install -y nodejs npm && npm install -g @anthropic-ai/claude-code
  curl -fsSL https://claude.ai/install.sh | bash
fi
export PATH="$HOME/.local/bin:$PATH"
claude --version || { echo "claude 설치 확인 실패 — docs.claude.com 설치 안내 참고"; exit 1; }

echo "[4/6] Python venv + 봇 의존성"
cd "$REPO"
[ -d .venv ] || python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip
./.venv/bin/pip install --quiet -r bot/requirements.txt

echo "[5/6] firecrawl MCP(원격 HTTP) 등록 — .env 에 FIRECRAWL_API_KEY 있을 때만"
FCKEY="$(grep -E '^FIRECRAWL_API_KEY=' "$REPO/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"' ')"
if [ -n "${FCKEY:-}" ]; then
  claude mcp add --scope user --transport http firecrawl \
    "https://mcp.firecrawl.dev/${FCKEY}/v2/mcp" 2>/dev/null \
    && echo "  firecrawl MCP 등록 완료" \
    || echo "  (이미 등록돼 있거나 실패 — claude mcp list 로 확인)"
else
  echo "  FIRECRAWL_API_KEY 미설정 — researcher 는 WebSearch/WebFetch 로만 동작(심화 스크랩 비활성)."
fi

echo "[6/6] systemd 유닛 설치 + 기동"
sudo cp "$REPO/deploy/gncausreport-bot.service"   /etc/systemd/system/
sudo cp "$REPO/deploy/gncausreport-brief.service" /etc/systemd/system/
sudo cp "$REPO/deploy/gncausreport-brief.timer"   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gncausreport-bot.service
sudo systemctl enable --now gncausreport-brief.timer

echo
echo "✅ 완료. 다음으로 확인:"
echo "   claude -p 'say hi'                          # API키 인증 동작 확인"
echo "   systemctl status gncausreport-bot --no-pager # 봇 가동 상태"
echo "   systemctl list-timers gncausreport-brief     # 다음 실행 예정 시각"
echo "   journalctl -u gncausreport-bot -f            # 봇 실시간 로그"
