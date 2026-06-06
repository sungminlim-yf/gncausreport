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

※ 2단계는 승인 플로우가 본 기능이다. 3단계 Q&A 답변 생성(LLM 호출)은 QA_ENABLED=1 일 때만 활성화되며
   기본은 침묵한다(미완성 답변으로 실제 글 스레드를 오염시키지 않기 위함). 답변 생성은 3단계에서 채운다.

실행: python bot/server.py   (사전: pip install -r bot/requirements.txt, .env 에 토큰 설정)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from datetime import datetime, timedelta

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ARCHIVE_DIR = os.path.join(REPO_ROOT, "archive")
RUN_INDEX = os.path.join(ARCHIVE_DIR, "run-index.json")
TOPICS_FILE = os.path.join(REPO_ROOT, "topics.md")
# 슬랙에서 즉시 조사 트리거 시 실행할 claude CLI 경로(.env CLAUDE_BIN 으로 덮어쓰기 가능)
CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "/Users/limsungmin/.local/bin/claude")

# 공유 Block Kit 렌더러(레포 루트) — 본 게시도 send.py 와 동일하게 마크다운→블록 렌더링
sys.path.insert(0, REPO_ROOT)
import slack_blocks  # noqa: E402


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


def read_report(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def post_report(client, channel: str, text: str) -> dict:
    """보고서를 Block Kit 으로 게시한다(가독성). 보통 단일 메시지(블록 ≤50).

    50블록 초과 시에만 첫 메시지 스레드에 이어 붙인다. 첫(루트) 메시지 ts 가
    3단계 Q&A 스레드 루트(D9). 반환: 첫(루트) 메시지의 {channel, ts}.
    """
    groups = slack_blocks.chunk_blocks(slack_blocks.render_blocks(text))
    fb = slack_blocks.fallback_text(text)
    root = client.chat_postMessage(channel=channel, text=fb, blocks=groups[0])
    root_ts = root["ts"]
    for group in groups[1:]:
        client.chat_postMessage(channel=root["channel"], text=fb, blocks=group, thread_ts=root_ts)
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

    # 초안 메시지 갱신(버튼 제거 + 결과 표시)
    client.chat_update(
        channel=channel,
        ts=body["message"]["ts"],
        text=f"✅ 승인됨 — <#{target}> 채널에 본 게시 완료 (by <@{user}>)",
        blocks=[],
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


# ── 3단계: 스레드 Q&A (D4·D9) — QA_ENABLED=1 일 때만 활성 ────────────────
def _qa_enabled() -> bool:
    return os.environ.get("QA_ENABLED", "").lower() in ("1", "true", "yes")


@app.event("app_mention")
def handle_mention(event, say):
    if _qa_enabled():
        _answer_question(event, say)


@app.event("message")
def handle_message(event, say):
    # 스레드 답글(thread_ts 존재)만 Q&A 후보. 봇 자신/일반 채팅은 무시.
    if event.get("bot_id") or not event.get("thread_ts"):
        return
    if _qa_enabled():
        _answer_question(event, say)


def _answer_question(event: dict, say) -> None:
    thread_ts = event.get("thread_ts") or event.get("ts")
    archive_path = find_archive_for_thread(thread_ts)
    if not archive_path:
        return  # 근거 없는(미승인) 스레드는 침묵 — 바인딩된 글 스레드만 응답
    # TODO(3단계): read_report(archive_path) 본문+📎출처를 근거로 LLM 답변 생성 → 출처 포함 회신.
    #              답변에도 반드시 원문 링크 포함. 현재는 골격 회신.
    say(text="(3단계 준비 중) 이 기사 근거로 출처 포함 답변을 회신하도록 구현 예정.", thread_ts=thread_ts)


def _notify_ops(client, msg: str) -> None:
    """운영자 채널 알림 (D19)."""
    ch = ops_channel_id()
    if ch:
        client.chat_postMessage(channel=ch, text=f"[bot] {msg}")


# ── 슬랙 제어: 즉시 조사 트리거 + 주제 관리 (/gnc 슬래시 명령) ─────────────
# 모바일 포함 슬랙에서 직접 파이프라인을 트리거하고 topics.md 를 편집한다.
# 지정 승인자(D17)만 사용 가능. 본 게시는 여전히 사람 승인을 거친다(D2).
def _topic_index() -> tuple[list[str], list[tuple[int, str]]]:
    """topics.md 전체 줄과, 그중 '주제 줄'(주석/블록인용 제외 · '|' 포함)의 (인덱스, 내용)."""
    lines = open(TOPICS_FILE, "r", encoding="utf-8").read().splitlines()
    topics = [(i, ln) for i, ln in enumerate(lines)
              if ln.strip() and not ln.lstrip().startswith(("#", ">")) and "|" in ln]
    return lines, topics


def topics_list() -> str:
    _, topics = _topic_index()
    if not topics:
        return "_(등록된 정기 주제가 없습니다)_"
    return "\n".join(f"`{n + 1}.` {ln.strip()}" for n, (_, ln) in enumerate(topics))


def _normalize_topic(spec: str) -> str:
    """'주제 | 채널 | depth' 입력을 정규화(채널 기본 exec-team, depth 기본 medium)."""
    parts = [p.strip() for p in spec.split("|")]
    topic = parts[0]
    channel = parts[1] if len(parts) > 1 and parts[1] else "exec-team"
    depth = parts[2] if len(parts) > 2 and parts[2] else "medium"
    return f"{topic} | {channel} | {depth}"


def topics_add(spec: str) -> str:
    line = _normalize_topic(spec)
    lines = open(TOPICS_FILE, "r", encoding="utf-8").read().splitlines()
    lines.append(line)
    open(TOPICS_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return line


def topics_rm(n: int) -> str | None:
    lines, topics = _topic_index()
    if n < 1 or n > len(topics):
        return None
    idx, content = topics[n - 1]
    del lines[idx]
    open(TOPICS_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return content.strip()


def _run_brief_async(topic: str, channel: str, depth: str, respond) -> None:
    """헤드리스 /brief 를 백그라운드 스레드에서 실행(스케줄러와 동일 경로). 완료 시 회신."""
    def work() -> None:
        prompt = f"/brief {topic} {channel} --depth {depth}"
        try:
            r = subprocess.run(
                [CLAUDE_BIN, "-p", prompt, "--dangerously-skip-permissions"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=1800,
            )
            if r.returncode == 0:
                respond(f"✅ 조사 완료 — 스테이징 채널의 *초안+승인버튼*을 확인하세요: *{topic}*")
            else:
                respond(f"⚠️ 조사 실패(코드 {r.returncode}): *{topic}* — 로그를 확인하세요.")
        except Exception as e:  # noqa: BLE001
            respond(f"⚠️ 조사 오류: *{topic}* — {e}")
    threading.Thread(target=work, daemon=True).start()


def _gnc_help() -> str:
    return (
        "*GNC 리포트 봇 명령* (지정 승인자만)\n"
        "• `/gnc brief <주제>` — 지금 바로 조사 → 스테이징 초안+승인버튼\n"
        "• `/gnc topics` — 정기 주제 목록\n"
        "• `/gnc topic add <주제> | <채널> | <depth>` — 주제 추가(채널·depth 생략 시 exec-team/medium)\n"
        "• `/gnc topic rm <번호>` — 주제 삭제\n"
        "• `/gnc help` — 도움말"
    )


@app.command("/gnc")
def handle_gnc(ack, command, respond):
    ack()  # 3초 내 즉시 ack (D4)
    user = command.get("user_id", "")
    if not is_approver(user):
        respond("⛔ 권한이 없습니다(지정 승인자만 사용 가능).")
        return

    text = (command.get("text") or "").strip()
    parts = text.split(maxsplit=1)
    sub = parts[0].lower() if parts else "help"
    rest = parts[1].strip() if len(parts) > 1 else ""

    if sub == "brief":
        if not rest:
            respond("사용법: `/gnc brief <주제>` (대상=exec-team, depth=medium)")
            return
        respond(f"🔎 조사를 시작합니다: *{rest}*\n수 분 뒤 스테이징 채널에 초안+승인버튼이 올라옵니다.")
        _run_brief_async(rest, "exec-team", "medium", respond)
    elif sub in ("topics", "topic"):
        if sub == "topics" or not rest or rest.startswith("list"):
            respond("📋 현재 정기 주제 (월·수·금 08:00 실행):\n" + topics_list())
        elif rest.startswith("add "):
            respond("➕ 주제 추가됨:\n`" + topics_add(rest[4:].strip()) + "`")
        elif rest.split(maxsplit=1)[0] in ("rm", "remove", "del"):
            arg = rest.split(maxsplit=1)
            try:
                n = int(arg[1].strip()) if len(arg) > 1 else 0
            except ValueError:
                respond("사용법: `/gnc topic rm <번호>`")
                return
            removed = topics_rm(n)
            respond(f"🗑️ 삭제됨: `{removed}`" if removed else f"{n}번 주제가 없습니다. `/gnc topics`로 번호 확인.")
        else:
            respond("사용법: `/gnc topic list` · `/gnc topic add <주제> | <채널> | <depth>` · `/gnc topic rm <번호>`")
    else:
        respond(_gnc_help())


if __name__ == "__main__":
    app_token = os.environ.get("SLACK_APP_TOKEN")
    if not app_token:
        raise SystemExit("SLACK_APP_TOKEN(xapp-) 미설정(.env). Socket Mode 연결에 필요합니다(D13).")
    handler = SocketModeHandler(app, app_token)
    print("[bot] Socket Mode 연결 시작 — 승인 버튼/이벤트 수신 대기 (Ctrl+C 종료)")
    handler.start()  # 아웃바운드 WebSocket — 공개 HTTPS 불필요 (D13)
