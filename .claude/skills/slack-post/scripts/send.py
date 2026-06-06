#!/usr/bin/env python3
"""
slack-post 전송 스크립트 (D10 · D19 · D21, v3)

보고서 파일을 슬랙 채널에 전송한다.
전송 추상화: --transport webhook(1단계 스캐폴드) | webapi(2단계+ 승인·스레드)
모든 비밀정보는 .env(환경변수)에서만 읽는다 — 하드코딩 금지.

사용:
  # 1단계: Incoming Webhook으로 테스트 채널 바로 게시
  python send.py --file archive/2026-06-06_주제.md --channel-alias exec-team --stage final --transport webhook

  # 2단계+: Web API로 스테이징 채널에 초안+승인버튼 게시
  python send.py --file ... --channel-alias staging --stage draft --transport webapi --run-id 2026-06-06_x

종료코드: 0 성공 / 1 실패(오케스트레이터가 운영자 채널 알림 트리거)

webapi 전송은 slack_sdk가 있으면 사용하고, 없으면 표준 라이브러리로 chat.postMessage 를 직접 호출한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

# 슬랙 단일 텍스트 블록 권장 한도(보수적). 초과 시 분할 전송(D10).
MAX_CHARS = 2900
SLACK_POST_URL = "https://slack.com/api/chat.postMessage"


def load_env() -> None:
    """.env 를 가볍게 로드(외부 의존성 없이). 이미 설정된 환경변수는 덮어쓰지 않음."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, "..", "..", "..", ".."))
    env_path = os.path.join(root, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


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


def approval_blocks(text: str, run_id: str) -> list[dict]:
    """초안 메시지에 부착할 [승인]/[반려] Block Kit 버튼(D5)."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
        {
            "type": "actions",
            "block_id": "approval_actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "✅ 승인"},
                    "action_id": "approve_post",
                    "value": run_id,
                },
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "❌ 반려"},
                    "action_id": "reject_post",
                    "value": run_id,
                },
            ],
        },
    ]


def post_webhook(text: str) -> None:
    """1단계 스캐폴드: Incoming Webhook 단방향 전송(승인·스레드 불가, D21)."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        die("SLACK_WEBHOOK_URL 미설정(.env).")
    for chunk in split_message(text):
        _http_post(url, {"text": chunk}, headers={"Content-Type": "application/json"})


def post_webapi(text: str, channel_alias: str, stage: str, run_id: str) -> None:
    """2단계+: chat.postMessage 로 게시. stage=draft 면 승인 버튼 부착(D5)."""
    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        die("SLACK_BOT_TOKEN(xoxb-) 미설정(.env). Web API는 2단계에서 활성화.")
    channel = resolve_channel_id(channel_alias)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Bearer {token}",
    }
    chunks = split_message(text)
    for i, chunk in enumerate(chunks):
        payload: dict = {"channel": channel, "text": chunk}
        # 초안의 마지막(또는 유일) 청크에만 승인 버튼을 부착
        if stage == "draft" and i == len(chunks) - 1:
            payload["blocks"] = approval_blocks(chunk, run_id)
        resp = _http_post(SLACK_POST_URL, payload, headers=headers)
        body = json.loads(resp)
        if not body.get("ok"):
            die(f"chat.postMessage 오류: {body.get('error')}")


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
    ap.add_argument("--channel-alias", required=True, help="채널 별칭(exec-team/staging/ops...)")
    ap.add_argument("--stage", choices=["draft", "final"], default="final",
                    help="draft=스테이징 초안(+승인버튼), final=본 게시")
    ap.add_argument("--run-id", default="", help="승인 버튼 value에 담을 run-id(draft 시 필수)")
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
            die("draft 게시에는 --run-id 가 필요합니다(승인 버튼 value).")
        post_webapi(text, args.channel_alias, args.stage, args.run_id)

    print(f"[slack-post] OK: {args.channel_alias}({args.stage}/{args.transport}) ← {args.file}")


if __name__ == "__main__":
    main()
