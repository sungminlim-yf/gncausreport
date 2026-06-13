#!/usr/bin/env python3
"""
슬랙 봇 (2단계 승인 + 3단계 Q&A 공용) — Socket Mode 상시 프로세스 (D13, v3)

핵심 설계:
  - Socket Mode (D13): 아웃바운드 WebSocket으로 슬랙과 연결 → 공개 HTTPS·고정 도메인·인증서 불필요.
    NAT/방화벽 뒤·로컬에서도 가동. App Token(xapp-)으로 연결, Bot Token(xoxb-)으로 API 호출.
  - 즉시 ack (D4): 슬랙 이벤트는 3초 내 ack 필요 → 핸들러에서 먼저 ack, 무거운 작업은 이후 처리.
  - 승인 인가 (D17): 지정 승인자 화이트리스트만 [승인]/[반려] 버튼 유효.
  - 스레드 바인딩 (D9): 게시물 스레드(thread_ts)의 질문 → 해당 게시물 archive 근거로 답변.

2↔3단계 바인딩(D9)은 archive/run-index.json 을 통해 이뤄진다(send.py 가 draft 게시 시 기록):
  {run_id: {archive, target_alias, draft{channel,ts}, approved{channel,ts,by}, status}}
  - 승인: 버튼 value(run_id) → 인덱스에서 archive 조회 → 본 게시 → approved{ts} 기록(스레드 루트).
  - Q&A: 스레드 thread_ts == 어떤 run 의 approved.ts 면 그 archive 를 근거로 답변(3단계).

※ 3단계 Q&A 답변 생성(LLM 호출)은 QA_ENABLED=1 일 때만 활성화되며, 기본은 침묵한다
   (오답으로 실제 글 스레드를 오염시키지 않기 위함). 답변은 헤드리스 `claude -p`로 해당 archive 전문만
   근거로 생성하고(웹검색 등 도구 미사용), 출처를 포함해 스레드에 비동기 회신한다(D4·D9).

실행: python bot/server.py   (사전: pip install -r bot/requirements.txt, .env 에 토큰 설정)
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
RUN_INDEX = os.path.join(ARCHIVE_DIR, "run-index.json")
# topics.md 의 경로·파싱·상태는 scripts/topics_tool.py(tt) 가 단일 관리한다.
# 슬랙에서 즉시 조사 트리거 시 실행할 claude CLI 경로.
# 우선순위: .env CLAUDE_BIN > PATH 상의 claude > (구) macOS 기본 경로. → Mac·Linux 양쪽 이식 가능.
CLAUDE_BIN = (
    os.environ.get("CLAUDE_BIN")
    or shutil.which("claude")
    or "/Users/limsungmin/.local/bin/claude"
)

# 공유 Block Kit 렌더러(레포 루트) — 본 게시도 send.py 와 동일하게 마크다운→블록 렌더링
sys.path.insert(0, REPO_ROOT)
import slack_blocks  # noqa: E402

# 이메일 발송(Resend) — 승인된 보고서를 지정 메일 리스트로(별도 버튼, D2 확장)
# python bot/server.py 실행 시엔 같은 디렉터리, 패키지 import 시엔 bot.email_sender 로 잡힘.
try:
    from bot import email_sender  # noqa: E402
except ImportError:  # pragma: no cover
    import email_sender  # type: ignore  # noqa: E402


# 주제 계획표(topics.md) 단일 관리 헬퍼 — run_topics.sh 와 공용(파싱·요일·상태 로직 일원화)
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
import topics_tool as tt  # noqa: E402


def _load_dotenv() -> None:
    """.env 를 가볍게 로드(외부 의존성 없이). 이미 설정된 환경변수는 덮어쓰지 않음."""
    env_path = os.path.join(REPO_ROOT, ".env")
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


_load_dotenv()
# token_verification_enabled=False: 생성 시점 auth.test 를 건너뛴다(부팅 순서·오프라인 테스트 허용).
# 토큰 유효성은 첫 API 호출에서 드러나며, App Token 존재는 아래 __main__ 에서 명시 확인한다.
app = App(token=os.environ.get("SLACK_BOT_TOKEN"), token_verification_enabled=False)


# ── 인가: 승인자 화이트리스트 (D17) ────────────────────────────────────
def is_approver(user_id: str) -> bool:
    allow = os.environ.get("SLACK_APPROVERS", "")
    return user_id in {u.strip() for u in allow.split(",") if u.strip()}


def resolve_alias_channel(alias: str) -> str:
    key = "SLACK_CHANNEL_" + alias.upper().replace("-", "_")
    return os.environ.get(key, "")


def ops_channel_id() -> str:
    return os.environ.get("SLACK_CHANNEL_OPS", "")


# ── run-index: send.py 와 공유하는 매핑 (2↔3단계 바인딩 SSOT) ────────────
def load_run_index() -> dict:
    if os.path.exists(RUN_INDEX):
        try:
            with open(RUN_INDEX, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_run_index(idx: dict) -> None:
    os.makedirs(os.path.dirname(RUN_INDEX), exist_ok=True)
    with open(RUN_INDEX, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def _abs_archive(path: str) -> str:
    return path if os.path.isabs(path) else os.path.join(REPO_ROOT, path)


def find_archive_for_run(run_id: str) -> str | None:
    """승인 버튼 value(run-id)로 본 게시할 archive 파일을 찾는다(run-index 조회)."""
    entry = load_run_index().get(run_id)
    if not entry or not entry.get("archive"):
        return None
    path = _abs_archive(entry["archive"])
    return path if os.path.exists(path) else None


def find_archive_for_thread(thread_ts: str) -> str | None:
    """게시물 스레드 루트(thread_ts)에 바인딩된 archive 파일을 찾는다(D9).

    run-index 에서 approved.ts == thread_ts 인 항목을 찾는다.
    최근 ARCHIVE_ACTIVE_MONTHS 개월만 활성 대상(D24).
    """
    months = int(os.environ.get("ARCHIVE_ACTIVE_MONTHS", "6"))
    cutoff = datetime.now() - timedelta(days=30 * months)
    for entry in load_run_index().values():
        approved = entry.get("approved") or {}
        if approved.get("ts") != thread_ts:
            continue
        path = entry.get("archive", "")
        base = os.path.basename(path)
        try:  # archive 파일명 앞 10자리(YYYY-MM-DD)로 활성 기간 판정(D24)
            if datetime.strptime(base[:10], "%Y-%m-%d") < cutoff:
                return None
        except ValueError:
            pass
        full = _abs_archive(path)
        return full if os.path.exists(full) else None
    return None


def _report_title(path: str) -> str:
    """보고서 첫 '# 제목' 줄을 라벨로. 없으면 파일명."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("# "):
                    return line[2:].strip()
    except OSError:
        pass
    return os.path.basename(path)


def active_archives(limit: int = 10) -> list[tuple[str, str]]:
    """최근 ARCHIVE_ACTIVE_MONTHS(D24) 내 승인·게시된 보고서 (title, path) 목록, 최신순.

    채널 레벨 질문의 그라운딩 후보. 비용·지연을 묶기 위해 최신 limit 건으로 제한한다.
    """
    months = int(os.environ.get("ARCHIVE_ACTIVE_MONTHS", "6"))
    cutoff = datetime.now() - timedelta(days=30 * months)
    items: list[tuple[datetime, str]] = []
    for entry in load_run_index().values():
        if not (entry.get("approved") and entry.get("status") == "approved"):
            continue
        path = entry.get("archive", "")
        base = os.path.basename(path)
        try:  # 파일명 앞 10자리(YYYY-MM-DD)로 활성 기간 판정(D24)
            dt = datetime.strptime(base[:10], "%Y-%m-%d")
        except ValueError:
            dt = datetime.min
        if dt != datetime.min and dt < cutoff:
            continue
        full = _abs_archive(path)
        if os.path.exists(full):
            items.append((dt, full))
    items.sort(key=lambda x: x[0], reverse=True)
    return [(_report_title(p), p) for _, p in items[:limit]]


def read_report(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def post_report(client, channel: str, text: str) -> dict:
    """보고서를 Block Kit 으로 게시한다(가독성). 보통 단일 메시지(블록 ≤50).

    50블록 초과 시에만 첫 메시지 스레드에 이어 붙인다. 첫(루트) 메시지 ts 가
    3단계 Q&A 스레드 루트(D9). 반환: 첫(루트) 메시지의 {channel, ts}.
    """
    groups = slack_blocks.chunk_blocks(slack_blocks.render_blocks(text))
    # 뉴스레터 맨 아래: 이번 주 진행현황 로드맵(요일 단위)을 마지막 그룹에 첨부.
    roster = slack_blocks.roster_blocks(tt.render_roster_mrkdwn())
    if roster:
        if len(groups[-1]) + len(roster) <= slack_blocks.MAX_BLOCKS:
            groups[-1] = groups[-1] + roster
        else:
            groups.append(roster)
    fb = slack_blocks.fallback_text(text)
    root = client.chat_postMessage(channel=channel, text=fb, blocks=groups[0],
                                   unfurl_links=False, unfurl_media=False)
    root_ts = root["ts"]
    for group in groups[1:]:
        client.chat_postMessage(channel=root["channel"], text=fb, blocks=group, thread_ts=root_ts,
                                unfurl_links=False, unfurl_media=False)
    return {"channel": root["channel"], "ts": root_ts}


# ── 2단계: 승인/반려 버튼 (D2·D5·D17) ──────────────────────────────────
@app.action("approve_post")
def handle_approve(ack, body, client, logger):
    ack()  # 3초 내 즉시 ack (D4)
    user = body["user"]["id"]
    run_id = body["actions"][0]["value"]
    channel = body["channel"]["id"]

    if not is_approver(user):
        # 화이트리스트 외 클릭 무시 + 에페메럴 안내 (오발·무단 게시 차단)
        client.chat_postEphemeral(channel=channel, user=user, text="⛔ 승인 권한이 없습니다(지정 승인자만 가능).")
        return

    archive_path = find_archive_for_run(run_id)
    if not archive_path:
        _notify_ops(client, f"승인했으나 archive 를 찾지 못함: run-id={run_id}")
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ archive 를 찾지 못했습니다(run-id={run_id}). 운영자에게 알렸습니다.")
        return

    idx = load_run_index()
    entry = idx.get(run_id, {})
    target_alias = entry.get("target_alias", "exec-team")
    target = resolve_alias_channel(target_alias)
    if not target:
        _notify_ops(client, f"승인했으나 대상 채널 미설정: alias={target_alias} (run-id={run_id})")
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ 대상 채널({target_alias})이 .env에 설정되지 않았습니다.")
        return

    # 대상 채널에 본 게시(분할·스레드 묶음). 첫 메시지 ts 가 3단계 Q&A 스레드 루트(D9).
    posted = post_report(client, target, read_report(archive_path))
    entry["approved"] = {"channel": posted["channel"], "ts": posted["ts"], "by": user}
    entry["status"] = "approved"
    idx[run_id] = entry
    save_run_index(idx)

    # 초안 메시지 갱신: 승인/반려 버튼은 제거하되 '📧 이메일 발송'은 남긴다(아직 미발송 시).
    # → 승인 후에도 메일 리스트 발송을 별도로 진행할 수 있음(승인과 독립, D2 확장).
    client.chat_update(
        channel=channel,
        ts=body["message"]["ts"],
        text=f"✅ 승인됨 — <#{target}> 채널에 본 게시 완료 (by <@{user}>)",
        blocks=slack_blocks.status_action_blocks(
            run_id, approved=True, emailed=bool(entry.get("emailed")),
            target=target, approver=user),
    )


@app.action("reject_post")
def handle_reject(ack, body, client):
    ack()
    user = body["user"]["id"]
    channel = body["channel"]["id"]
    run_id = body["actions"][0]["value"]
    if not is_approver(user):
        client.chat_postEphemeral(channel=channel, user=user, text="⛔ 승인 권한이 없습니다(지정 승인자만 가능).")
        return

    idx = load_run_index()
    if run_id in idx:
        idx[run_id]["status"] = "rejected"
        idx[run_id]["rejected_by"] = user
        save_run_index(idx)

    client.chat_update(
        channel=channel,
        ts=body["message"]["ts"],
        text=f"❌ 반려됨 (by <@{user}>) — 수정 후 재게시 필요",
        blocks=[],
    )


# ── 2단계 확장: 이메일 발송 (Resend) — 승인과 독립된 별도 버튼 ──────────────
@app.action("email_send")
def handle_email_send(ack, body, client, logger):
    ack()  # 3초 내 즉시 ack (D4)
    user = body["user"]["id"]
    run_id = body["actions"][0]["value"]
    channel = body["channel"]["id"]
    msg_ts = body["message"]["ts"]

    if not is_approver(user):
        client.chat_postEphemeral(channel=channel, user=user, text="⛔ 발송 권한이 없습니다(지정 승인자만 가능).")
        return

    # 설정 점검(미설정이면 친절히 안내 — 봇은 죽지 않음)
    ok, reason = email_sender.email_configured()
    if not ok:
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ 이메일이 아직 설정되지 않았습니다: {reason}\n(.env 의 RESEND_API_KEY · EMAIL_FROM · EMAIL_RECIPIENTS 확인)")
        return

    archive_path = find_archive_for_run(run_id)
    if not archive_path:
        _notify_ops(client, f"이메일 발송 요청했으나 archive 미발견: run-id={run_id}")
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ archive 를 찾지 못했습니다(run-id={run_id}). 운영자에게 알렸습니다.")
        return

    idx = load_run_index()
    entry = idx.get(run_id, {})
    if entry.get("emailed"):
        client.chat_postEphemeral(channel=channel, user=user, text="ℹ️ 이미 이메일로 발송된 보고서입니다(중복 방지).")
        return

    title = _report_title(archive_path)
    # 뉴스레터 맨 아래: 이번 주 진행현황 로드맵(요일 단위)을 HTML 카드로 본문 뒤에 첨부.
    result = email_sender.send_report_email(
        subject=title, markdown_body=read_report(archive_path),
        extra_html=tt.render_roster_html())
    if not result.get("ok"):
        _notify_ops(client, f"이메일 발송 실패(run-id={run_id}): {result.get('detail')}")
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ 이메일 발송 실패: {result.get('detail')}")
        return

    # 발송 성공 기록(중복 방지) + 메시지 갱신(이메일 버튼 제거, 승인 버튼은 상태대로 유지)
    entry["emailed"] = {"by": user, "count": result.get("count"), "id": result.get("id")}
    idx[run_id] = entry
    save_run_index(idx)

    approved = entry.get("approved") or {}
    client.chat_update(
        channel=channel, ts=msg_ts,
        text=f"📧 이메일 발송 완료 — 수신자 {result.get('count')}명 (by <@{user}>)",
        blocks=slack_blocks.status_action_blocks(
            run_id, approved=bool(approved), emailed=True,
            target=approved.get("channel"), approver=approved.get("by")),
    )
    client.chat_postEphemeral(channel=channel, user=user,
                              text=f"📧 이메일 발송 완료 — 수신자 {result.get('count')}명에게 보냈습니다.")


# ── 주간 주제 안내 발송 (토요일 갱신 통보의 [📧 수신자에게 발송] 버튼) ──────
@app.action("email_topics")
def handle_email_topics(ack, body, client, logger):
    ack()  # 3초 내 즉시 ack (D4)
    user = body["user"]["id"]
    channel = body["channel"]["id"]
    msg_ts = body["message"]["ts"]

    if not is_approver(user):
        client.chat_postEphemeral(channel=channel, user=user, text="⛔ 발송 권한이 없습니다(지정 승인자만 가능).")
        return

    ok, reason = email_sender.email_configured()
    if not ok:
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ 이메일이 아직 설정되지 않았습니다: {reason}")
        return

    # 현재 topics.md(=토요일 갱신으로 교체된 다음 주 계획)를 안내 메일로 발송.
    subject, md = tt.render_topics_email_md()
    result = email_sender.send_report_email(subject=subject, markdown_body=md)
    if not result.get("ok"):
        _notify_ops(client, f"주간 주제 안내 발송 실패: {result.get('detail')}")
        client.chat_postEphemeral(channel=channel, user=user,
                                  text=f"⚠️ 발송 실패: {result.get('detail')}")
        return

    cnt = result.get("count")
    cnt_txt = f"수신자 {cnt}명" if cnt else "수신자 메일 리스트"
    # 메시지 갱신: 발송 버튼 제거(중복 발송 방지) + 발송 완료 표시. 주제 요약 블록은 유지.
    kept = [b for b in body["message"].get("blocks", []) if b.get("block_id") != "topics_email_actions"]
    kept.append({"type": "context", "elements": [
        {"type": "mrkdwn", "text": f"📧 발송 완료 — {cnt_txt} (by <@{user}>)"}]})
    client.chat_update(channel=channel, ts=msg_ts,
                       text=f"📧 다음 주 주제 안내 발송 완료 — {cnt_txt} (by <@{user}>)", blocks=kept)
    client.chat_postEphemeral(channel=channel, user=user,
                              text=f"📧 다음 주 주제 안내를 {cnt_txt}에게 발송했습니다.")


# ── 3단계: Q&A (D4·D9) — QA_ENABLED=1 일 때만 활성 ──────────────────────
# 두 경로를 모두 지원:
#   (1) 게시된 글 스레드 질문 → 그 글만 근거(정밀 바인딩, D9).
#   (2) 채널에 직접 올린 질문 → 활성 보고서 전체에서 관련 글을 찾아 답(슬랙 미숙 사용자 배려).
#       (2)의 답변은 스레드+채널 동시 노출(reply_broadcast)로 비전문 사용자도 바로 보게 한다.
def _qa_enabled() -> bool:
    return os.environ.get("QA_ENABLED", "").lower() in ("1", "true", "yes")


def _qa_channels() -> set[str]:
    """채널 레벨(스레드 아님) 질문을 받을 채널 ID 집합.
    QA_CHANNELS(쉼표) 미설정 시 본 게시 대상인 exec-team 채널을 기본 허용."""
    chans = {c.strip() for c in os.environ.get("QA_CHANNELS", "").split(",") if c.strip()}
    if not chans:
        exec_ch = resolve_alias_channel("exec-team")
        if exec_ch:
            chans.add(exec_ch)
    return chans


# app_mention 과 message 이벤트가 같은 메시지에 모두 발화할 수 있어 (channel,ts)로 1회만 처리.
_qa_seen: set[str] = set()
_qa_lock = threading.Lock()


def _seen_once(channel: str, ts: str) -> bool:
    """(channel,ts) 이벤트를 이미 처리했으면 True. 메모리 누수 방지로 상한 초과 시 비운다."""
    key = f"{channel}:{ts}"
    with _qa_lock:
        if key in _qa_seen:
            return True
        _qa_seen.add(key)
        if len(_qa_seen) > 1000:
            _qa_seen.clear()
        return False


# 채널 레벨 오발 방지용 질문 신호 — 물음표 또는 의문 키워드(한/영). 멘션·스레드엔 적용 안 함.
_Q_HINTS = ("?", "？", "까", "나요", "인가요", "무엇", "뭐", "뭔", "어디", "언제", "얼마",
            "왜", "어떻게", "어떤", "무슨", "몇", "알려", "궁금", "가능", "있나", "있어",
            "되나", "될까", "무어", "what", "when", "where", "why", "how", "who", "which")


def _looks_like_question(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _Q_HINTS)


@app.event("app_mention")
def handle_mention(event, client, logger):
    if _qa_enabled():  # 멘션은 명시적 의도 → 질문 휴리스틱 생략
        _answer_question(event, client, logger)


@app.event("message")
def handle_message(event, client, logger):
    if event.get("bot_id") or event.get("subtype") or not _qa_enabled():
        return  # 봇 자신/편집·삭제(subtype)/Q&A 비활성은 무시
    if event.get("thread_ts"):
        _answer_question(event, client, logger)                 # (1) 스레드 답글
    elif event.get("channel") in _qa_channels() and _looks_like_question(event.get("text", "")):
        _answer_question(event, client, logger)                 # (2) 허용 채널의 채널 레벨 질문


def _clean_question(text: str) -> str:
    """슬랙 멘션 토큰(<@U…>)을 제거하고 질문 본문만 남긴다."""
    return re.sub(r"<@[^>]+>", "", text or "").strip()


def _answer_question(event: dict, client, logger) -> None:
    thread_ts = event.get("thread_ts") or event.get("ts")
    channel = event.get("channel", "")
    if _seen_once(channel, event.get("ts", "")):
        return
    question = _clean_question(event.get("text", ""))
    if not question:
        return
    # 그라운딩 선택: 스레드가 특정 글에 바인딩되면 그 글만(정밀, D9),
    # 아니면(채널 레벨·비바인딩 스레드) 활성 보고서 전체에서 관련 글을 찾아 답한다.
    bound = find_archive_for_thread(thread_ts)
    if bound:
        reports = [bound]
    else:
        reports = [p for _, p in active_archives()]
        if not reports:
            return  # 근거 보고서가 없으면 침묵
    broadcast = not event.get("thread_ts")  # 채널 레벨 질문은 채널에도 보이게(reply_broadcast)
    threading.Thread(
        target=_qa_work,
        args=(client, channel, thread_ts, reports, question, broadcast, logger),
        daemon=True,
    ).start()


_QA_PROMPT = """당신은 사내 보고서에 대한 슬랙 질문에 답하는 어시스턴트입니다.
아래 [보고서] 전문만을 근거로 [질문]에 한국어로 답하세요. 웹 검색 등 도구는 쓰지 말고 보고서 내용만 사용합니다.

규칙:
- 보고서에 적힌 사실·수치·날짜만 사용하고, 없는 내용은 추측하지 않는다.
- 경영진 대상이므로 결론을 먼저, 전체 3~6줄로 간결히 답한다.
- 핵심 수치·발효일은 보고서 표기 그대로 인용한다.
- 답변 끝에 한 줄로 근거 출처를 보고서 📎출처의 [번호]·URL로 표기한다(예: 출처 [2] https://...).
- 보고서로 답할 수 없으면 다른 말 없이 정확히 `__NO_REPORT_ANSWER__` 한 줄만 출력한다(설명·추측 금지).
- 슬랙 메시지이므로 인사·머리말 없이 답만 출력한다.

[보고서]
{report}

[질문]
{question}
"""

_QA_PROMPT_MULTI = """당신은 사내 보고서들에 대한 슬랙 질문에 답하는 어시스턴트입니다.
아래 [보고서들] 중 질문과 관련된 보고서를 찾아 그 내용만 근거로 [질문]에 한국어로 답하세요. 웹 검색 등 도구는 쓰지 말고 보고서 내용만 사용합니다.

규칙:
- 여러 보고서 중 질문에 해당하는 보고서를 골라 그 내용·수치·날짜만 사용하고, 없는 내용은 추측하지 않는다.
- 경영진 대상이므로 결론을 먼저, 전체 3~6줄로 간결히 답한다.
- 핵심 수치·발효일은 보고서 표기 그대로 인용한다.
- 답변 끝에 한 줄로 근거 출처를 해당 보고서 📎출처의 [번호]·URL로 표기한다(예: 출처 [2] https://...).
- 어느 보고서로도 답할 수 없으면 다른 말 없이 정확히 `__NO_REPORT_ANSWER__` 한 줄만 출력한다(설명·추측 금지).
- 슬랙 메시지이므로 인사·머리말 없이 답만 출력한다.

[보고서들]
{reports}

[질문]
{question}
"""

# 보고서로 못 답할 때 그라운딩 LLM 이 내보내는 마커 → 봇이 웹 검색 폴백으로 전환.
_NO_ANSWER = "__NO_REPORT_ANSWER__"

_QA_PROMPT_WEB = """당신은 한국 지엔씨에너지(GNC Energy) 경영진을 돕는 어시스턴트입니다.
사내 보고서에 없는 질문이므로, 웹 검색을 사용해 [질문]에 한국어로 답하세요.

규칙:
- 필요하면 웹 검색으로 최신·신뢰할 수 있는 정보를 찾는다.
- 경영진 대상이므로 결론을 먼저, 전체 3~6줄로 간결히 답한다.
- 핵심 수치·날짜에는 가능하면 출처(기관/매체)와 URL을 한 줄로 덧붙인다.
- 확실하지 않으면 불확실하다고 밝힌다. 추측을 사실처럼 적지 않는다.
- 슬랙 메시지이므로 인사·머리말 없이 답만 출력한다.

[질문]
{question}
"""


def _web_fallback_enabled() -> bool:
    """보고서로 못 답할 때 웹 검색 폴백 사용 여부(기본 on). QA_WEB_FALLBACK=0 으로 끔."""
    return os.environ.get("QA_WEB_FALLBACK", "1").strip().lower() not in ("0", "false", "no")


def _run_claude(prompt: str, timeout: int | None = None) -> str:
    """헤드리스 `claude -p`로 답변 생성(스케줄러·트리거와 동일 런타임 — 새 의존성/API키 불필요)."""
    cmd = [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions"]
    model = os.environ.get("QA_MODEL", "").strip()
    if model:  # 비용·지연을 낮추려면 .env 의 QA_MODEL 로 더 가벼운 모델 지정 가능
        cmd += ["--model", model]
    to = timeout if timeout is not None else int(os.environ.get("QA_TIMEOUT", "180"))
    r = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=to)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p 실패(code {r.returncode}): {(r.stderr or '')[:300]}")
    return (r.stdout or "").strip()


def _generate_answer(report_paths: list[str], question: str) -> str:
    """보고서 1개면 단일 근거, 여러 개면 관련 글을 골라 답한다. 못 답하면 _NO_ANSWER 마커 반환."""
    if len(report_paths) == 1:
        return _run_claude(_QA_PROMPT.format(report=read_report(report_paths[0]), question=question))
    blocks = [f"===== 보고서 {i}: {_report_title(p)} =====\n{read_report(p)}"
              for i, p in enumerate(report_paths, 1)]
    return _run_claude(_QA_PROMPT_MULTI.format(reports="\n\n".join(blocks), question=question))


def _web_answer(question: str) -> str:
    """보고서에 없는 질문 — 웹 검색 기반 일반 답변(라벨은 호출부에서 부착). 웹은 더 오래 걸려 타임아웃 여유."""
    timeout = int(os.environ.get("QA_WEB_TIMEOUT", "300"))
    return _run_claude(_QA_PROMPT_WEB.format(question=question), timeout=timeout)


def _qa_work(client, channel: str, thread_ts: str, report_paths: list[str],
             question: str, broadcast: bool, logger) -> None:
    """진행 표시 게시 → 답변 생성 → 같은 메시지를 답변으로 갱신. 실패 시 운영자 알림(D19).

    broadcast=True(채널 레벨 질문)면 스레드 답변을 채널에도 노출(reply_broadcast) — 슬랙 미숙 사용자 배려."""
    extra = {"reply_broadcast": True} if broadcast else {}
    placeholder_ts = None
    try:
        ph = client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text="💬 게시된 보고서를 근거로 답변을 작성하는 중입니다…",
            unfurl_links=False, unfurl_media=False, **extra,
        )
        placeholder_ts = ph["ts"]
    except Exception:  # noqa: BLE001 — 진행 표시는 실패해도 본 답변 생성은 계속
        logger.exception("Q&A 진행 표시 게시 실패")

    def _set_progress(text: str) -> None:
        if placeholder_ts:
            try:
                client.chat_update(channel=channel, ts=placeholder_ts, text=text)
            except Exception:  # noqa: BLE001
                pass

    # 1) 보고서 그라운딩 답변 (못 답하면 _NO_ANSWER 마커)
    answer = None
    try:
        grounded = _generate_answer(report_paths, question)
        if grounded and _NO_ANSWER not in grounded:
            answer = grounded
    except Exception as e:  # noqa: BLE001
        logger.exception("Q&A 보고서 답변 생성 실패")
        _notify_ops(client, f"Q&A 보고서 답변 실패: thread={thread_ts} — {e}")

    # 2) 보고서로 못 답하면 웹 검색 폴백 → 출처가 보고서가 아님을 명확히 라벨링
    if answer is None and _web_fallback_enabled():
        _set_progress("🔎 보고서에 없는 내용이라 웹에서 찾아보는 중입니다…")
        try:
            web = _web_answer(question)
            if web:
                answer = ("🔎 *보고서에 없는 내용 — Claude 웹 검색 기반 답변입니다* "
                          "_(사내 보고서로 검증된 내용 아님)_\n\n" + web)
        except Exception as e:  # noqa: BLE001
            logger.exception("Q&A 웹 폴백 실패")
            _notify_ops(client, f"Q&A 웹 폴백 실패: thread={thread_ts} — {e}")

    if answer is None:
        answer = "⚠️ 지금은 답변을 생성하지 못했습니다. 잠시 후 다시 질문해 주세요."

    if placeholder_ts:
        client.chat_update(channel=channel, ts=placeholder_ts, text=answer)
    else:
        client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=answer,
                                unfurl_links=False, unfurl_media=False, **extra)


def _notify_ops(client, msg: str) -> None:
    """운영자 채널 알림 (D19)."""
    ch = ops_channel_id()
    if ch:
        client.chat_postMessage(channel=ch, text=f"[bot] {msg}",
                                unfurl_links=False, unfurl_media=False)


# ── 슬랙 제어: 즉시 조사 트리거 + 주제 보기/갱신 (/gnc 슬래시 명령) ─────────
# 모바일 포함 슬랙에서 직접 파이프라인을 트리거한다. 주제 추가/삭제는 폐지(주차 자동 선정으로 대체).
# 본 게시는 여전히 사람 승인을 거친다(D2). topics.md 의 파싱·상태는 topics_tool 이 단일 관리.
def topics_list() -> str:
    """요일·상태 포함 사람용 목록(슬랙 표시). topics_tool 이 단일 소스."""
    return tt.cmd_list()


_DAY_KO = {"Mon": "월", "Tue": "화", "Wed": "수", "Thu": "목", "Fri": "금", "Sat": "토", "Sun": "일"}
_WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]
_CAL_RE = re.compile(r"OnCalendar=(\S+)\s")
_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2}):(\d{2})")
_SEOUL = ZoneInfo("Asia/Seoul")


def _fmt_seoul(naive_local: str, tzname: str) -> str:
    """systemd 다음실행 문자열(시스템 tz)을 서울(KST) 기준 직관적 한국어로.
    예: '월요일 2026년 6월 8일 오전 7시'. 파싱 실패 시 ''."""
    m = _TS_RE.search(naive_local)
    if not m:
        return ""
    y, mo, d, hh, mm, ss = (int(x) for x in m.groups())
    try:
        local = datetime(y, mo, d, hh, mm, ss, tzinfo=ZoneInfo(tzname))
    except Exception:  # noqa: BLE001
        return ""
    s = local.astimezone(_SEOUL)
    ampm = "오전" if s.hour < 12 else "오후"
    h12 = s.hour % 12 or 12
    t = f"{ampm} {h12}시" + (f" {s.minute}분" if s.minute else "")
    return f"{_WEEKDAY_KO[s.weekday()]} {s.year}년 {s.month}월 {s.day}일 {t}"


def schedule_summary() -> str:
    """정기 실행 스케줄을 systemd 타이머에서 *실시간* 조회(하드코딩 금지).
    실제 가동 중인 타이머가 진실의 원천 → 봇 응답과 스케줄이 어긋나지 않는다.
    요일 cadence + 다음 실행시각(서울/KST 직관 표기)을 돌려준다.
    systemd 미사용 환경(개발 맥 등)·조회 실패 시 빈 문자열 반환(문구 생략)."""
    try:
        cal = subprocess.run(
            ["systemctl", "show", "gncausreport-brief.timer", "-p", "TimersCalendar", "--value"],
            capture_output=True, text=True, timeout=5).stdout
        nxt = subprocess.run(
            ["systemctl", "show", "gncausreport-brief.timer", "-p", "NextElapseUSecRealtime", "--value"],
            capture_output=True, text=True, timeout=5).stdout.strip()
        tzname = subprocess.run(
            ["timedatectl", "show", "-p", "Timezone", "--value"],
            capture_output=True, text=True, timeout=5).stdout.strip() or "Australia/Sydney"
    except Exception:  # noqa: BLE001
        return ""
    days: list[str] = []
    for spec in _CAL_RE.findall(cal):
        days.extend(spec.split(","))
    if not days:
        return ""
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    uniq = sorted(set(days), key=lambda d: order.index(d) if d in order else 99)
    ko = "·".join(_DAY_KO.get(d, d) for d in uniq)
    out = f"{ko} 정기 실행"
    nxt_ko = _fmt_seoul(nxt, tzname)
    if nxt_ko:
        out += f" · 다음 {nxt_ko} (서울)"
    return out


def topics_random() -> tuple[str, str, str] | None:
    """랜덤 조사용 주제 1건 — 현재 9개 + 과거 이력(topics-history)에서 추출. 없으면 None.
    이미 지나가 빠진 주제도 풀에 포함된다(운영자 요구 — 지난 주제도 조사 가능)."""
    e = tt.random_pool()
    return (e["topic"], e["channel"], e["depth"]) if e else None


# ── 일일 조사 트리거 한도(구독 토큰 보호) ────────────────────────────────
# `/지엔씨 조사`는 멤버 누구나 쓸 수 있고 승인 없이 바로 게시되므로(D2 예외),
# 폭주로 구독 토큰이 한 번에 소진되는 것만 막는 **느슨한 안전 상한**(사실상 무제한 운용).
DAILY_TRIGGER_LIMIT = 100
_QUOTA_FILE = os.path.join(os.path.expanduser("~"), ".gncausreport-quota.json")
_QUOTA_LOCK = threading.Lock()


def _quota_check_and_inc() -> tuple[bool, int]:
    """오늘(서울 날짜) `/지엔씨 조사` 트리거 수를 +1. (허용여부, 오늘 사용횟수) 반환.
    한도 초과 시 (False, 현재횟수)로 미증가. 봇 재시작에도 유지되도록 파일에 보존(레포 밖 HOME)."""
    today = datetime.now(_SEOUL).strftime("%Y-%m-%d")
    with _QUOTA_LOCK:
        try:
            with open(_QUOTA_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:  # noqa: BLE001
            d = {}
        if d.get("date") != today:           # 날짜 바뀌면 리셋(서울 자정 기준)
            d = {"date": today, "count": 0}
        if d["count"] >= DAILY_TRIGGER_LIMIT:
            return False, d["count"]
        d["count"] += 1
        try:
            with open(_QUOTA_FILE, "w", encoding="utf-8") as f:
                json.dump(d, f)
        except Exception:  # noqa: BLE001
            pass
        return True, d["count"]


def _run_brief_async(topic: str, channel: str, depth: str, respond, publish: bool = False) -> None:
    """헤드리스 /brief 를 백그라운드 스레드에서 실행(스케줄러와 동일 경로). 완료 시 회신.
    publish=True 면 --publish 로 *검수 통과 시* 승인 없이 대상 채널 직접 게시(D2 예외)."""
    def work() -> None:
        prompt = f"/brief {topic} {channel} --depth {depth}"
        if publish:
            prompt += " --publish"
        try:
            r = subprocess.run(
                [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
            )
            if r.returncode == 0:
                if publish:
                    respond(f"✅ 조사 완료: *{topic}* — 검수 통과 시 *{channel}* 채널에 게시됨"
                            "(검수 미통과 시 스테이징에 보류).")
                else:
                    respond(f"✅ 조사 완료 — 스테이징 채널의 *초안+승인버튼*을 확인하세요: *{topic}*")
            else:
                respond(f"⚠️ 조사 실패(코드 {r.returncode}): *{topic}* — 로그를 확인하세요.")
        except Exception as e:  # noqa: BLE001
            respond(f"⚠️ 조사 오류: *{topic}* — {e}")
    threading.Thread(target=work, daemon=True).start()


def _run_refresh_async(mode_args: str, respond) -> None:
    """헤드리스 `/refresh-topics <mode_args>` 를 백그라운드로 실행. 주제 갱신 후 슬랙 통보는
    refresh-topics 가 topics_tool notify 로 직접 수행. 여기서는 트리거 응답만 회신한다."""
    def work() -> None:
        prompt = f"/refresh-topics {mode_args}".strip()
        try:
            r = subprocess.run(
                [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
            )
            if r.returncode == 0:
                respond("✅ 주제 갱신 완료 — `/지엔씨 주제`로 갱신된 목록을 확인하세요.")
            else:
                respond(f"⚠️ 주제 갱신 실패(코드 {r.returncode}) — 로그를 확인하세요.")
        except Exception as e:  # noqa: BLE001
            respond(f"⚠️ 주제 갱신 오류 — {e}")
    threading.Thread(target=work, daemon=True).start()


# 한/영 명령 키워드 (슬랙 슬래시 명령은 `/gnc` 와 `/지엔씨` 둘 다 같은 핸들러로 라우팅)
_BRIEF_KW = {"brief", "조사", "조사해", "리서치"}
_TOPICS_KW = {"topics", "topic", "주제"}
_REFRESH_KW = {"주제갱신", "갱신", "리프레시", "refresh", "refresh-topics"}
_HELP_KW = {"help", "도움", "도움말", "?"}


def _gnc_help() -> str:
    return (
        "*GNC 리포트 봇 명령* (한/영 모두 가능)\n"
        f"• `/지엔씨 조사 <주제>` — 지금 바로 조사 → 검수 통과 시 *exec-team* 채널에 바로 게시 _(누구나 · 하루 {DAILY_TRIGGER_LIMIT}회)_\n"
        "• `/지엔씨 조사`  (주제 생략) — 현재 주제 + *과거 주제 이력*에서 무작위 1개 조사(shallow)\n"
        "• `/지엔씨 주제` — 이번 주 정기 주제(요일·진행상태) + 다음 실행 시각 _(누구나)_\n"
        "• `/지엔씨 주제갱신` — 아직 조사 안 된 주제만 새로 교체 _(승인자만 · 토요일 전체 자동갱신과 별개)_\n"
        "• `/지엔씨 도움` — 도움말"
    )


def _handle_gnc(ack, command, respond):
    ack()  # 3초 내 즉시 ack (D4)
    user = command.get("user_id", "")
    text = (command.get("text") or "").strip()
    tokens = text.split()
    head = tokens[0].lower() if tokens else "help"

    # 조사 / brief — 멤버 누구나 가능(개방). 하루 총 한도 적용 + 검수 통과 시 승인 없이 바로 게시(D2 예외).
    if head in _BRIEF_KW:
        ok, used = _quota_check_and_inc()
        if not ok:
            respond(f"⛔ 오늘 `/지엔씨 조사` 일일 한도({DAILY_TRIGGER_LIMIT}회)에 도달했습니다."
                    " 서울 자정 이후 다시 시도하세요. _(구독 토큰 보호)_")
            return
        topic = text[len(tokens[0]):].strip()
        if not topic:
            picked = topics_random()
            if not picked:
                respond("조사할 주제가 없습니다(현재 주제·과거 이력 모두 비어 있음). 토요일 자동 갱신을 기다리거나 `/지엔씨 주제갱신`을 요청하세요.")
                return
            topic, channel, _depth = picked
            depth = "shallow"   # 랜덤 조사는 비용 보호를 위해 shallow 강제
            respond(f"🎲 무작위 주제: *{topic}* _(채널 {channel} · depth {depth})_ · 오늘 {used}/{DAILY_TRIGGER_LIMIT}회\n"
                    f"검수 통과 시 *{channel}* 채널에 바로 게시됩니다(수 분 소요).")
            _run_brief_async(topic, channel, depth, respond, publish=True)
            return
        respond(f"🔎 조사 시작: *{topic}* · 오늘 {used}/{DAILY_TRIGGER_LIMIT}회\n"
                "검수 통과 시 *exec-team* 채널에 바로 게시됩니다(수 분 소요).")
        _run_brief_async(topic, "exec-team", "medium", respond, publish=True)
        return

    # 주제갱신 (단일 토큰) — 아직 조사 안 된 주제만 교체(주중 보충). 승인자만(D17).
    # ↓ 누구나 열고 싶으면 아래 is_approver 가드를 제거하면 됨(운영자 판단).
    if head in _REFRESH_KW:
        if not is_approver(user):
            respond("⛔ 주제 갱신은 지정 승인자만 가능합니다. (조사는 누구나 `/지엔씨 조사`)")
            return
        respond("🔄 아직 조사 안 된 주제를 새로 교체 중입니다… (이미 조사된 주제는 유지) _수 분 소요_")
        _run_refresh_async("--replace-pending", respond)
        return

    # 주제 / topic — 두 번째 토큰이 '갱신'이면 주제 갱신, 아니면 목록(요일·상태)
    if head in _TOPICS_KW:
        action = tokens[1].lower() if len(tokens) > 1 else "list"
        if action in _REFRESH_KW:  # `/지엔씨 주제 갱신`
            if not is_approver(user):
                respond("⛔ 주제 갱신은 지정 승인자만 가능합니다. (조사는 누구나 `/지엔씨 조사`)")
                return
            respond("🔄 아직 조사 안 된 주제를 새로 교체 중입니다… (이미 조사된 주제는 유지) _수 분 소요_")
            _run_refresh_async("--replace-pending", respond)
            return
        # 목록(기본): 누구나
        sched = schedule_summary()
        header = f"📋 이번 주 정기 주제 ({sched}):" if sched else "📋 이번 주 정기 주제:"
        respond(header + "\n" + topics_list())
        return

    respond(_gnc_help())


# 영어·한국어 명령 이름 모두 같은 핸들러로 라우팅
app.command("/gnc")(_handle_gnc)
app.command("/지엔씨")(_handle_gnc)


if __name__ == "__main__":
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise SystemExit("SLACK_APP_TOKEN(xapp-) 미설정(.env). Socket Mode 연결에 필요합니다(D13).")
    handler = SocketModeHandler(app, app_token)
    print("[bot] Socket Mode 연결 시작 — 승인 버튼/이벤트 수신 대기 (Ctrl+C 종료)")
    handler.start()  # 아웃바운드 WebSocket — 공개 HTTPS 불필요 (D13)
