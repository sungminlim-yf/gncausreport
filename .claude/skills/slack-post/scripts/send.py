#!/usr/bin/env python3
"""
slack-post 전송 스크립트 (D10 · D19 · D21, v3 / 2단계)

보고서 파일을 슬랙 채널에 전송한다.
전송 추상화: --transport webhook(1단계 스캐폴드) | webapi(2단계+ 승인·스레드)
모든 비밀정보는 .env(환경변수)에서만 읽는다 — 하드코딩 금지.

사용:
  # 1단계: Incoming Webhook으로 테스트 채널 바로 게시
  python send.py --file archive/2026-06-06_주제.md --channel-alias exec-team --stage final --transport webhook

  # 2단계+: Web API로 스테이징 채널에 초안+승인버튼 게시 (승인 시 target-alias 채널에 본 게시)
  python send.py --file archive/2026-06-06_주제.md --channel-alias staging \
      --stage draft --transport webapi --run-id 2026-06-06_x --target-alias exec-team

종료코드: 0 성공 / 1 실패(오케스트레이터가 운영자 채널 알림 트리거)

2단계 바인딩(D9):
  draft 게시 시 archive/run-index.json 에 {run_id: {archive, target_alias, draft{channel,ts}, status}} 를 기록한다.
  봇(bot/server.py)이 승인 버튼 value(run_id)로 이 인덱스를 조회해 본 게시할 archive 파일을 찾고,
  본 게시 후 approved{channel,ts} 를 같은 인덱스에 추가해 3단계 Q&A 스레드 바인딩의 근거로 쓴다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

# 공유 Block Kit 렌더러(레포 루트) 임포트 — 마크다운 보고서 → 슬랙 블록
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))
import slack_blocks  # noqa: E402

# 슬랙 단일 텍스트 블록 권장 한도(보수적). 초과 시 분할 전송(D10).
MAX_CHARS = 2900
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


def repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "..", "..", ".."))


def load_env() -> None:
    """.env 를 가볍게 로드(외부 의존성 없이). 이미 설정된 환경변수는 덮어쓰지 않음."""
    env_path = os.path.join(repo_root(), ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# ── run-index: run_id ↔ archive·게시 메타 매핑 (2↔3단계 바인딩 SSOT) ──────
def run_index_path() -> str:
    return os.path.join(repo_root(), "archive", "run-index.json")


def load_run_index() -> dict:
    p = run_index_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_run_index(idx: dict) -> None:
    p = run_index_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def resolve_channel_id(alias: str) -> str:
    """채널 별칭(exec-team/staging/ops...) → 실제 채널 ID. .env의 SLACK_CHANNEL_<별칭>."""
    key = "SLACK_CHANNEL_" + alias.upper().replace("-", "_")
    cid = os.environ.get(key)
    if not cid:
        die(f"채널 ID 미설정: .env에 {key} 를 추가하세요.")
    return cid


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """길면 문단 경계 기준으로 분할(D10)."""
    if len(text) <= limit:
        return [text]
    chunks, buf = [], ""
    for para in text.split("\n\n"):
        if len(buf) + len(para) + 2 > limit and buf:
            chunks.append(buf.rstrip())
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        chunks.append(buf.rstrip())
    n = len(chunks)
    if n > 1:
        chunks = [f"{c}\n\n— ({i + 1}/{n})" for i, c in enumerate(chunks)]
    return chunks


def post_webhook(text: str) -> None:
    """1단계 스캐폴드: Incoming Webhook 단방향 전송(승인·스레드 불가, D21)."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        die("SLACK_WEBHOOK_URL 미설정(.env).")
    for chunk in split_message(text):
        _http_post(url, {"text": chunk}, headers={"Content-Type": "application/json"})


def post_webapi(text: str, channel_alias: str, stage: str, run_id: str,
                target_alias: str, archive_file: str) -> None:
    """2단계+: chat.postMessage 로 Block Kit 게시. stage=draft 면 승인버튼 부착 + run-index 기록(D5·D9)."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        die("SLACK_BOT_TOKEN(xoxb-) 미설정(.env). Web API는 2단계에서 활성화.")
    channel = resolve_channel_id(channel_alias)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
    }
    # 마크다운 보고서 → Block Kit. 보통 1그룹(단일 메시지), 50블록 초과 시 분할.
    groups = slack_blocks.chunk_blocks(slack_blocks.render_blocks(text))
    fb = slack_blocks.fallback_text(text)
    draft_ts = None
    for i, group in enumerate(groups):
        is_last = i == len(groups) - 1
        blocks = list(group)
        # 초안의 마지막(또는 유일) 그룹에만 승인 버튼을 부착
        if stage == "draft" and is_last:
            blocks = blocks + slack_blocks.approval_action_blocks(run_id)
        payload = {"channel": channel, "text": fb, "blocks": blocks}
        body = json.loads(_http_post(SLACK_POST_URL, payload, headers=headers))
        if not body.get("ok"):
            die(f"chat.postMessage 오류: {body.get('error')}")
        if is_last:
            draft_ts = body.get("ts")

    # 초안이면 봇이 승인 시 조회할 수 있도록 run-index 에 매핑 기록(D9)
    if stage == "draft":
        idx = load_run_index()
        idx[run_id] = {
            "archive": archive_file,            # 본 게시할 보고서 파일(레포 상대경로)
            "target_alias": target_alias,       # 승인 시 본 게시할 채널 별칭
            "draft": {"channel": channel, "ts": draft_ts},
            "status": "draft",
        }
        save_run_index(idx)


def _http_post(url: str, payload: dict, headers: dict) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            if resp.status >= 300:
                die(f"슬랙 응답 오류: {resp.status} {raw[:200]!r}")
            return raw
    except urllib.error.URLError as e:
        die(f"슬랙 전송 실패: {e}")


def die(msg: str) -> None:
    print(f"[slack-post] 실패: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description="슬랙 보고서 전송")
    ap.add_argument("--file", required=True, help="전송할 보고서 파일 경로")
    ap.add_argument("--channel-alias", required=True, help="게시 채널 별칭(final=대상 / draft=staging)")
    ap.add_argument("--stage", choices=["draft", "final"], default="final",
                    help="draft=스테이징 초안(+승인버튼), final=본 게시")
    ap.add_argument("--run-id", default="", help="승인 버튼 value/인덱스 키에 담을 run-id(draft 시 필수)")
    ap.add_argument("--target-alias", default="exec-team",
                    help="draft 승인 시 본 게시할 대상 채널 별칭(기본 exec-team, D20)")
    ap.add_argument("--transport", choices=["webhook", "webapi"],
                    default=os.environ.get("SLACK_TRANSPORT", "webhook"),
                    help="전송 방식(기본 .env의 SLACK_TRANSPORT)")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        die(f"파일 없음: {args.file}")
    with open(args.file, "r", encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        die("빈 파일 — 전송할 내용 없음.")

    if args.transport == "webhook":
        if args.stage == "draft":
            die("webhook 방식은 draft(승인 버튼) 미지원 — webapi 사용(D21).")
        post_webhook(text)
    else:
        if args.stage == "draft" and not args.run_id:
            die("draft 게시에는 --run-id 가 필요합니다(승인 버튼 value/인덱스 키).")
        post_webapi(text, args.channel_alias, args.stage, args.run_id, args.target_alias, args.file)

    print(f"[slack-post] OK: {args.channel_alias}({args.stage}/{args.transport}) ← {args.file}")


if __name__ == "__main__":
    main()
