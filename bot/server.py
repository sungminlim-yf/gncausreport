#!/usr/bin/env python3
"""
슬랙 봇 (2단계 승인 + 3단계 Q&A 공용) — Socket Mode 상시 프로세스 (D13, v3)

핵심 설계:
  - Socket Mode (D13): 아웃바운드 WebSocket으로 슬랙과 연결 → 공개 HTTPS·고정 도메인·인증서 불필요.
    NAT/방화벽 뒤·로컬에서도 가동. App Token(xapp-)으로 연결, Bot Token(xoxb-)으로 API 호출.
  - 즉시 ack (D4): 슬랙 이벤트는 3초 내 ack 필요 → 핸들러에서 먼저 ack, 무거운 작업은 이후 처리.
  - 승인 인가 (D17): 지정 승인자 화이트리스트만 [승인]/[반려] 버튼 유효.
  - 스레드 바인딩 (D9): 게시물 스레드(thread_ts)의 질문 → 해당 게시물 archive 근거로 답변.

※ 2·3단계 진입 시 활성화하는 골격이다. 답변 생성(Claude 호출)·archive 매핑 등 TODO를 채운다.
   1단계(D22)는 이 봇 없이 Webhook 게시만으로 동작한다.

실행: python bot/server.py   (사전: pip install -r bot/requirements.txt, .env 에 토큰 설정)
"""
from __future__ import annotations

import glob
import os
from datetime import datetime, timedelta

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")

app = App(token=os.environ.get("SLACK_BOT_TOKEN"))


# ── 인가: 승인자 화이트리스트 (D17) ────────────────────────────────────
def is_approver(user_id: str) -> bool:
    allow = os.environ.get("SLACK_APPROVERS", "")
    return user_id in {u.strip() for u in allow.split(",") if u.strip()}


def target_channel_id() -> str:
    return os.environ.get("SLACK_CHANNEL_EXEC_TEAM", "")


def ops_channel_id() -> str:
    return os.environ.get("SLACK_CHANNEL_OPS", "")


# ── archive 근거 조회 (D9 스레드 바인딩 · D24 활성 보관) ────────────────
def find_archive_for_run(run_id: str) -> str | None:
    """승인 버튼 value(run-id)로 본 게시할 archive 파일을 찾는다."""
    if not run_id:
        return None
    hits = glob.glob(os.path.join(ARCHIVE_DIR, f"*{run_id}*.md"))
    return hits[0] if hits else None


def find_archive_for_thread(thread_ts: str) -> str | None:
    """게시물 스레드 루트(thread_ts)에 바인딩된 archive 파일을 찾는다.

    게시 시 archive 파일에 thread_ts 를 메타로 기록해두고 여기서 역참조한다(TODO: 매핑 저장).
    최근 ARCHIVE_ACTIVE_MONTHS 개월만 활성 대상(D24).
    """
    months = int(os.environ.get("ARCHIVE_ACTIVE_MONTHS", "6"))
    cutoff = datetime.now() - timedelta(days=30 * months)
    for path in sorted(glob.glob(os.path.join(ARCHIVE_DIR, "20*_*.md")), reverse=True):
        name = os.path.basename(path)
        try:
            if datetime.strptime(name[:10], "%Y-%m-%d") < cutoff:
                continue
        except ValueError:
            continue
        # TODO: thread_ts ↔ archive 매핑 테이블로 정확히 바인딩
        return path
    return None


def read_report(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── 2단계: 승인/반려 버튼 (D2·D5·D17) ──────────────────────────────────
@app.action("approve_post")
def handle_approve(ack, body, client, logger):
    ack()  # 3초 내 즉시 ack (D4)
    user = body["user"]["id"]
    run_id = body["actions"][0]["value"]
    if not is_approver(user):
        # 화이트리스트 외 클릭 무시 + 에페메럴 안내 (오발·무단 게시 차단)
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=user, text="승인 권한이 없습니다."
        )
        return

    archive_path = find_archive_for_run(run_id)
    if not archive_path:
        _notify_ops(client, f"승인했으나 archive를 찾지 못함: run-id={run_id}")
        return

    # 대상 채널에 본 게시
    posted = client.chat_postMessage(channel=target_channel_id(), text=read_report(archive_path))
    # 초안 메시지 갱신(버튼 제거 + 결과 표시)
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text=f"✅ 승인됨 — 대상 채널 게시 완료 (by <@{user}>)",
        blocks=[],
    )
    # TODO: thread_ts(posted["ts"]) ↔ archive_path 매핑 저장 → 3단계 Q&A 바인딩(D9)


@app.action("reject_post")
def handle_reject(ack, body, client):
    ack()
    user = body["user"]["id"]
    if not is_approver(user):
        client.chat_postEphemeral(
            channel=body["channel"]["id"], user=user, text="승인 권한이 없습니다."
        )
        return
    client.chat_update(
        channel=body["channel"]["id"],
        ts=body["message"]["ts"],
        text=f"❌ 반려됨 (by <@{user}>) — 수정 후 재게시 필요",
        blocks=[],
    )


# ── 3단계: 스레드 Q&A (D4·D9) ──────────────────────────────────────────
@app.event("app_mention")
def handle_mention(event, say, ack):
    # 게시물 스레드 안에서의 멘션을 해당 글 질문으로 처리
    _answer_question(event, say)


@app.event("message")
def handle_message(event, say):
    # 스레드 답글(thread_ts 존재)만 Q&A 후보로 처리, 봇 자신/일반 채팅은 무시
    if event.get("bot_id") or not event.get("thread_ts"):
        return
    _answer_question(event, say)


def _answer_question(event: dict, say) -> None:
    thread_ts = event.get("thread_ts") or event.get("ts")
    archive_path = find_archive_for_thread(thread_ts)
    if not archive_path:
        return  # 근거 없는 스레드는 침묵(또는 안내). 필요 시 researcher 재호출(§9 3단계)
    # TODO: read_report(archive_path) 본문+출처를 근거로 Claude로 답변 생성 → 출처 포함
    #       답변에도 반드시 원문 링크 포함. 아래는 골격 회신.
    say(
        text="(준비 중) 이 기사 근거로 답변하도록 구현 예정 — 출처 포함 회신",
        thread_ts=thread_ts,
    )


def _notify_ops(client, msg: str) -> None:
    """운영자 채널 알림 (D19)."""
    ch = ops_channel_id()
    if ch:
        client.chat_postMessage(channel=ch, text=f"[bot] {msg}")


if __name__ == "__main__":
    handler = SocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    handler.start()  # 아웃바운드 WebSocket — 공개 HTTPS 불필요 (D13)
