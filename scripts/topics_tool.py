#!/usr/bin/env python3
"""
topics_tool — 주차 계획표(topics.md) 단일 관리 헬퍼 (CLI + import 공용)

왜 한 곳에 모으나:
  run_topics.sh(bash)와 bot/server.py(python)가 각자 topics.md 를 파싱·수정하면 로직이
  표류한다. 한글·특수문자 안전한 read/write, 요일 슬롯/상태(pending·done) 규칙, 부분교체·
  전체교체를 **이 모듈 한 곳**이 책임진다. 양쪽이 CLI(서브프로세스) 또는 import 로 호출한다.

데이터 모델 (topics.md 한 줄 = 한 주제):
    <주제> | <채널> | <depth> | <요일> | <상태>
  - 요일: Mon / Wed / Fri  (정기 실행 요일. 슬롯당 3건 = 주 9건)
  - 상태: pending(미조사) / done(조사 실행 완료)
  - 4·5번째 컬럼이 없으면 Mon / pending 으로 간주(구 3컬럼 하위호환).
  - '#' 또는 '>' 로 시작하는 줄은 주석/헤더(무시).

빠지는 주제(전체·부분 교체로 제거)는 archive/topics-history.md 에 누적 →
  랜덤 조사 풀 + 반복 회피 근거로 재사용한다.

CLI:
  topics_tool.py today                  오늘(시드니) 요일의 pending 을 '주제 | 채널 | depth' 로 출력
  topics_tool.py list                   요일·상태 포함 사람용 목록(슬랙 표시용)
  topics_tool.py mark-done "<주제>"     해당 줄 상태를 done 으로
  topics_tool.py random-pool            현재 9개 + history 합집합에서 1건 '주제 | 채널 | depth'
  topics_tool.py apply-weekly <json>    9개 새 주제로 전체 교체(3·3·3 배분, 전부 pending), 기존 history 이관
  topics_tool.py apply-replace-pending <json>  done 보존, pending 슬롯만 교체, 빠진 pending history 이관
  topics_tool.py notify "<메시지>"      운영 채널에 단순 슬랙 통보(.env 토큰 사용)
  topics_tool.py notify-weekly          다음 주 주제 요약 + [📧 수신자에게 발송] 버튼을 통보 채널에 게시
  topics_tool.py roster                 이번 주 진행현황 로드맵(마크다운) 출력(디버그·미리보기)

apply-* 의 <json> 은 파일 경로. 형식:
  {"week": "2026-W25", "topics": [{"topic": "...", "channel": "exec-team", "depth": "medium"}, ...]}
  - apply-weekly: topics 9건 권장(앞에서부터 Mon3·Wed3·Fri3 배분).
  - apply-replace-pending: 현재 pending 슬롯 개수만큼(요일 순서대로 채움). week 키는 무시.
"""
from __future__ import annotations

import html
import json
import os
import random
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TOPICS_FILE = os.path.join(REPO_ROOT, "topics.md")
HISTORY_FILE = os.path.join(REPO_ROOT, "archive", "topics-history.md")

DAYS = ["Mon", "Wed", "Fri"]            # 정기 실행 요일(슬롯 순서)
PER_DAY = 3                             # 요일당 주제 수 → 주 9건
SYDNEY = ZoneInfo("Australia/Sydney")   # 정기 타이머가 시드니 기준이므로 요일도 시드니로 판정
DAY_KO = {"Mon": "월", "Wed": "수", "Fri": "금"}
# 파이썬 weekday()(월=0…일=6) 기준 요일 인덱스 — 로드맵 '요일 단위 진행' 판정용.
WEEKDAY_IDX = {"Mon": 0, "Wed": 2, "Fri": 4}
DEFAULT_CHANNEL = "exec-team"
DEFAULT_DEPTH = "medium"


# ── .env 로드 (notify 용 토큰) ──────────────────────────────────────────
def _load_env() -> None:
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


# ── 파싱 ────────────────────────────────────────────────────────────────
def _is_topic_line(line: str) -> bool:
    s = line.strip()
    return bool(s) and not s.startswith(("#", ">")) and "|" in s


def parse_line(line: str) -> dict:
    """'주제 | 채널 | depth | 요일 | 상태' 한 줄 → dict. 누락 컬럼은 기본값."""
    parts = [p.strip() for p in line.split("|")]
    topic = parts[0]
    channel = parts[1] if len(parts) > 1 and parts[1] else DEFAULT_CHANNEL
    depth = parts[2] if len(parts) > 2 and parts[2] else DEFAULT_DEPTH
    day = parts[3] if len(parts) > 3 and parts[3] in DAYS else "Mon"
    status = parts[4] if len(parts) > 4 and parts[4] in ("pending", "done") else "pending"
    return {"topic": topic, "channel": channel, "depth": depth, "day": day, "status": status}


def format_line(e: dict) -> str:
    return f"{e['topic']} | {e['channel']} | {e['depth']} | {e['day']} | {e['status']}"


def read_topics() -> tuple[list[str], list[tuple[int, dict]]]:
    """topics.md 전체 줄과, 그중 주제 줄의 (행번호, parsed) 목록."""
    if not os.path.exists(TOPICS_FILE):
        return [], []
    lines = open(TOPICS_FILE, "r", encoding="utf-8").read().splitlines()
    topics = [(i, parse_line(ln)) for i, ln in enumerate(lines) if _is_topic_line(ln)]
    return lines, topics


def _write_atomic(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# ── 헤더(주차) ──────────────────────────────────────────────────────────
def _current_iso_week() -> str:
    y, w, _ = datetime.now(SYDNEY).isocalendar()
    return f"{y}-W{w:02d}"


def _render_file(entries: list[dict], week: str | None = None) -> str:
    """주제 dict 목록을 요일별 섹션으로 묶어 topics.md 전체 텍스트로 렌더."""
    if week is None:
        week = _current_iso_week()
    today = datetime.now(SYDNEY).strftime("%Y-%m-%d")
    out = [
        "# 정기 실행 주제 계획표 (주차 단위 · 자동 갱신)",
        "",
        "> 한 줄 = 한 주제. 형식: `<주제> | <채널> | <depth> | <요일(Mon/Wed/Fri)> | <상태(pending/done)>`",
        "> `#`·`>` 로 시작하는 줄은 주석/헤더(무시). 빈 줄도 무시.",
        "> 토요일 08:00(시드니) `/refresh-topics --weekly --auto` 가 다음 주 9개를 자동 선정·배분(Mon·Wed·Fri 3건씩).",
        "> 정기 실행(run_topics.sh)은 그날 요일의 `pending` 만 조사하고 `done` 으로 표시함.",
        "> 빠진 주제는 archive/topics-history.md 에 누적(랜덤 조사 풀·반복 회피).",
        f"> 주차: {week} · 최근 갱신: {today}",
        "",
    ]
    for day in DAYS:
        out.append(f"# === {DAY_KO[day]}요일 ({day}) ===")
        for e in [x for x in entries if x["day"] == day]:
            out.append(format_line(e))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── history 이관 ────────────────────────────────────────────────────────
def _archive_history(entries: list[dict]) -> None:
    """제거되는 주제를 topics-history.md 에 누적(중복 주제는 건너뜀)."""
    if not entries:
        return
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    existing = ""
    if os.path.exists(HISTORY_FILE):
        existing = open(HISTORY_FILE, "r", encoding="utf-8").read()
    else:
        existing = ("# 과거 주제 이력 (topics-history)\n\n"
                    "> 주차 갱신·부분교체로 topics.md 에서 빠진 주제를 누적. "
                    "랜덤 조사(`/지엔씨 조사`) 풀 + 반복 회피(topic-curator) 근거로 재사용.\n"
                    "> 형식: `<주제> | <채널> | <depth>  (빠진날짜)`\n\n")
    seen = {ln.split("|")[0].strip() for ln in existing.splitlines() if _is_topic_line(ln)}
    today = datetime.now(SYDNEY).strftime("%Y-%m-%d")
    add = []
    for e in entries:
        if e["topic"] in seen:
            continue
        seen.add(e["topic"])
        add.append(f"{e['topic']} | {e['channel']} | {e['depth']}  ({today})")
    if add:
        _write_atomic(HISTORY_FILE, existing.rstrip() + "\n" + "\n".join(add) + "\n")


def _history_entries() -> list[dict]:
    if not os.path.exists(HISTORY_FILE):
        return []
    lines = open(HISTORY_FILE, "r", encoding="utf-8").read().splitlines()
    out = []
    for ln in lines:
        if not _is_topic_line(ln):
            continue
        # 끝의 '(YYYY-MM-DD)' 꼬리는 depth 파싱에 섞이지 않게 제거
        core = ln.split("(")[0].strip() if ln.rstrip().endswith(")") else ln
        out.append(parse_line(core))
    return out


# ── 명령 구현 ───────────────────────────────────────────────────────────
def cmd_today() -> str:
    """오늘(시드니) 요일의 pending 주제를 '주제 | 채널 | depth' 줄로 출력."""
    today_day = datetime.now(SYDNEY).strftime("%a")  # Mon/Tue/...
    _, topics = read_topics()
    rows = [e for _, e in topics if e["day"] == today_day and e["status"] == "pending"]
    return "\n".join(f"{e['topic']} | {e['channel']} | {e['depth']}" for e in rows)


def cmd_list() -> str:
    """요일·상태 포함 사람용 목록(슬랙 표시)."""
    _, topics = read_topics()
    if not topics:
        return "_(등록된 정기 주제가 없습니다 — 토요일 자동 갱신 또는 `/지엔씨 주제갱신` 대기)_"
    out = []
    for day in DAYS:
        rows = [e for _, e in topics if e["day"] == day]
        done = sum(1 for e in rows if e["status"] == "done")
        out.append(f"*{DAY_KO[day]}요일* _(완료 {done}/{len(rows)})_")
        for e in rows:
            mark = "✅" if e["status"] == "done" else "⏳"
            out.append(f"  {mark} {e['topic']} _({e['depth']})_")
    return "\n".join(out)


# ── 로드맵(진행현황) — 요일 단위 진행(Model C) ────────────────────────────
def weekly_progress() -> dict:
    """이번 주 진행현황을 '요일 단위'로 분류해 반환(뉴스레터 하단 로드맵용).

    같은 날 발송되는 보고서들이 '제각각 진행률'로 보이지 않도록, 개별 보고서의
    게시·발송 시점이 아니라 **달력상 요일 위치**로 완료/진행중/예정을 판정한다.
      - 지난 요일(오늘보다 앞): done   (그 요일 분량은 모두 발행 끝난 것으로 간주)
      - 오늘 요일: today (발송 진행중)
      - 다음 요일: upcoming (예정)
    → 그날치 3건은 모두 동일한 로드맵을 보여줌(시점 드리프트 없음).
    반환: {week, done, total, days:[{day, ko, state, topics:[{topic,depth},...]}]}
    """
    _, topics = read_topics()
    today_idx = datetime.now(SYDNEY).weekday()  # 월=0 … 일=6
    week = _existing_week() or _current_iso_week()
    days: list[dict] = []
    done = total = 0
    for day in DAYS:
        rows = [e for _, e in topics if e["day"] == day]
        total += len(rows)
        di = WEEKDAY_IDX[day]
        if di < today_idx:
            state = "done"
            done += len(rows)
        elif di == today_idx:
            state = "today"
        else:
            state = "upcoming"
        days.append({"day": day, "ko": DAY_KO[day], "state": state, "topics": rows})
    return {"week": week, "done": done, "total": total, "days": days}


_STATE_HEAD = {"done": "✅", "today": "🔄", "upcoming": "⏳"}
_STATE_NOTE = {"done": "", "today": " (오늘 · 발송 진행중)", "upcoming": " (예정)"}


def render_roster_md() -> str:
    """이메일 본문 하단에 붙일 진행현황 로드맵(마크다운)."""
    p = weekly_progress()
    if not p["total"]:
        return ""
    out = ["---", "",
           f"**📋 이번주 진행현황 ({p['done']}/{p['total']} 완료)** · 주차 {p['week']}", ""]
    for d in p["days"]:
        out.append(f"**{_STATE_HEAD[d['state']]} {d['ko']}요일{_STATE_NOTE[d['state']]}**")
        for e in d["topics"]:
            mark = "✅ " if d["state"] == "done" else ""
            out.append(f"- {mark}{e['topic']}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_roster_mrkdwn() -> str:
    """슬랙 본 게시 하단 컨텍스트 블록에 붙일 진행현황 로드맵(슬랙 mrkdwn)."""
    p = weekly_progress()
    if not p["total"]:
        return ""
    out = [f"*📋 이번주 진행현황 ({p['done']}/{p['total']} 완료)*  ·  주차 {p['week']}"]
    for d in p["days"]:
        out.append(f"{_STATE_HEAD[d['state']]} *{d['ko']}요일*{_STATE_NOTE[d['state']]}")
        for e in d["topics"]:
            mark = "✅ " if d["state"] == "done" else "• "
            out.append(f"   {mark}{e['topic']}")
    return "\n".join(out)


_STATE_COLOR = {"done": "#2e7d46", "today": "#1f6feb", "upcoming": "#8a8f98"}


def render_roster_html() -> str:
    """이메일용 진행현황 로드맵 — 마크다운 변환 의존 없이 깔끔한 HTML 카드.

    (마크다운 경로는 nl2br+리스트 충돌로 '- ' 가 글자로 새므로, 이메일은 HTML 로 직접 렌더)
    """
    p = weekly_progress()
    if not p["total"]:
        return ""
    parts = [
        "<div style=\"margin:20px 0 0;padding:16px 18px;background:#f6f8fb;"
        "border:1px solid #e6ebf1;border-radius:10px\">",
        "<div style=\"font-size:15px;font-weight:700;color:#222;margin-bottom:12px\">"
        f"📋 이번주 진행현황 <span style=\"color:#2e7d46\">{p['done']}/{p['total']} 완료</span>"
        f" <span style=\"color:#8a8f98;font-weight:400;font-size:13px\">· 주차 {html.escape(p['week'])}</span></div>",
    ]
    for d in p["days"]:
        color = _STATE_COLOR[d["state"]]
        parts.append(
            "<div style=\"margin-bottom:10px\">"
            f"<div style=\"font-weight:600;color:{color};font-size:14px;margin-bottom:3px\">"
            f"{_STATE_HEAD[d['state']]} {d['ko']}요일{_STATE_NOTE[d['state']]}</div>"
            "<ul style=\"margin:0;padding-left:20px;color:#444;font-size:14px;line-height:1.6\">"
        )
        for e in d["topics"]:
            parts.append(f"<li>{html.escape(e['topic'])}</li>")
        parts.append("</ul></div>")
    parts.append("</div>")
    return "".join(parts)


def render_topics_email_md() -> tuple[str, str]:
    """주간 주제 안내 이메일의 (제목, 마크다운 본문). 현재 topics.md(=다음 주 계획) 기준."""
    _, topics = read_topics()
    week = _existing_week() or _current_iso_week()
    subject = f"다음 주 정기 리포트 주제 안내 ({week})"
    out = [f"# {subject}", "",
           "다음 주 GnC 시장 리포트는 아래 주제로 **월·수·금**에 발행 예정입니다.", ""]
    for day in DAYS:
        rows = [e for _, e in topics if e["day"] == day]
        if not rows:
            continue
        out.append(f"## 📅 {DAY_KO[day]}요일")
        for e in rows:
            out.append(f"- {e['topic']}")
        out.append("")
    return subject, "\n".join(out).rstrip() + "\n"


def cmd_mark_done(topic: str) -> bool:
    """주어진 주제와 일치하는 첫 pending 줄을 done 으로. 성공 시 True."""
    topic = topic.strip()
    lines, topics = read_topics()
    for idx, e in topics:
        if e["topic"] == topic and e["status"] != "done":
            e["status"] = "done"
            lines[idx] = format_line(e)
            _write_atomic(TOPICS_FILE, "\n".join(lines) + "\n")
            return True
    return False


def random_pool() -> dict | None:
    """현재 주제 + history 합집합에서 무작위 1건. 없으면 None."""
    _, topics = read_topics()
    pool = [e for _, e in topics] + _history_entries()
    # 주제 텍스트 기준 중복 제거(현재 것 우선)
    uniq: dict[str, dict] = {}
    for e in pool:
        uniq.setdefault(e["topic"], e)
    items = list(uniq.values())
    return random.choice(items) if items else None


def cmd_random_pool() -> str:
    e = random_pool()
    return f"{e['topic']} | {e['channel']} | {e['depth']}" if e else ""


def _load_new_topics(json_path: str) -> tuple[list[dict], str | None]:
    data = json.load(open(json_path, "r", encoding="utf-8"))
    raw = data.get("topics", [])
    week = data.get("week")
    out = []
    for t in raw:
        out.append({
            "topic": str(t["topic"]).strip(),
            "channel": str(t.get("channel") or DEFAULT_CHANNEL).strip(),
            "depth": str(t.get("depth") or DEFAULT_DEPTH).strip(),
        })
    return out, week


def cmd_apply_weekly(json_path: str) -> str:
    """9개 새 주제로 전체 교체. 앞에서부터 Mon3·Wed3·Fri3 배분, 전부 pending. 기존 전량 history 이관."""
    new_topics, week = _load_new_topics(json_path)
    if len(new_topics) != DAYS.__len__() * PER_DAY:
        # 9개가 아니어도 진행하되, 슬롯 수만큼만 채운다(부족분은 그대로, 초과분은 버림).
        sys.stderr.write(f"[topics_tool] 경고: 주제 {len(new_topics)}건 (기대 {len(DAYS) * PER_DAY}건)\n")
    _, old = read_topics()
    _archive_history([e for _, e in old])
    entries: list[dict] = []
    for i, t in enumerate(new_topics[: len(DAYS) * PER_DAY]):
        t["day"] = DAYS[i // PER_DAY]
        t["status"] = "pending"
        entries.append(t)
    _write_atomic(TOPICS_FILE, _render_file(entries, week))
    return f"전체 갱신: {len(entries)}건 (Mon·Wed·Fri 배분), 기존 {len(old)}건 history 이관"


def cmd_apply_replace_pending(json_path: str) -> str:
    """done 보존, pending 슬롯만 새 주제로 교체(요일·개수 유지). 빠진 pending 은 history 이관."""
    new_topics, _ = _load_new_topics(json_path)
    _, topics = read_topics()
    entries = [e for _, e in topics]
    pending_idx = [i for i, e in enumerate(entries) if e["status"] == "pending"]
    replaced = []
    for slot, ti in zip(pending_idx, range(len(new_topics))):
        old_e = entries[slot]
        replaced.append(old_e)
        nt = new_topics[ti]
        entries[slot] = {"topic": nt["topic"], "channel": nt["channel"],
                         "depth": nt["depth"], "day": old_e["day"], "status": "pending"}
    _archive_history(replaced)
    # 주차 헤더는 유지(부분 교체이므로) → 현재 파일 헤더의 week 보존 시도
    week = _existing_week()
    _write_atomic(TOPICS_FILE, _render_file(entries, week))
    return (f"부분 갱신: pending {len(pending_idx)}건 중 {min(len(pending_idx), len(new_topics))}건 교체, "
            f"done {len(entries) - len(pending_idx)}건 보존")


def _existing_week() -> str | None:
    if not os.path.exists(TOPICS_FILE):
        return None
    for ln in open(TOPICS_FILE, "r", encoding="utf-8").read().splitlines():
        s = ln.strip()
        if s.startswith(">") and "주차:" in s:
            seg = s.split("주차:", 1)[1].strip()
            return seg.split()[0].strip() if seg else None
    return None


# ── 슬랙 통보 ────────────────────────────────────────────────────────────
def _notify_channel_id() -> str:
    """통보 채널: ops > staging > exec-team 순서로 .env 에서 해석."""
    for alias in ("ops", "staging", "exec-team"):
        key = "SLACK_CHANNEL_" + alias.upper().replace("-", "_")
        cid = os.environ.get(key)
        if cid:
            return cid
    return ""


def cmd_notify(message: str) -> str:
    _load_env()
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = _notify_channel_id()
    if not token or not channel:
        sys.stderr.write("[topics_tool] SLACK_BOT_TOKEN 또는 통보 채널 미설정 — 통보 생략\n")
        return ""
    payload = json.dumps({"channel": channel, "text": message,
                          "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        if '"ok":true' not in resp:
            sys.stderr.write(f"[topics_tool] 슬랙 통보 실패: {resp[:200]}\n")
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[topics_tool] 슬랙 통보 오류: {e}\n")
    return ""


def _post_blocks(text: str, blocks: list[dict]) -> bool:
    """통보 채널에 blocks 메시지 게시. 성공 True. (버튼 클릭은 상시 봇이 처리)"""
    _load_env()
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = _notify_channel_id()
    if not token or not channel:
        sys.stderr.write("[topics_tool] SLACK_BOT_TOKEN 또는 통보 채널 미설정 — 통보 생략\n")
        return False
    payload = json.dumps({"channel": channel, "text": text, "blocks": blocks,
                          "unfurl_links": False, "unfurl_media": False}).encode("utf-8")
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage", data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10).read().decode("utf-8")
        if '"ok":true' not in resp:
            sys.stderr.write(f"[topics_tool] 슬랙 통보 실패: {resp[:200]}\n")
            return False
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"[topics_tool] 슬랙 통보 오류: {e}\n")
        return False
    return True


def cmd_notify_weekly() -> str:
    """다음 주 주제 요약 + [📧 수신자에게 발송] 버튼을 통보 채널에 게시.

    토요일 자동 갱신(`/refresh-topics --weekly --auto`)이 topics.md 를 다음 주 9건으로
    교체한 뒤 호출한다. 버튼(action_id=email_topics) 클릭은 상시 봇(bot/server.py)이 처리해
    현재 topics.md 를 수신자 메일 리스트로 발송한다(승인자 전용).
    """
    _, topics = read_topics()
    week = _existing_week() or _current_iso_week()
    lines = [f"📅 다음 주({week}) 정기 리포트 주제 {len(topics)}건이 확정되었습니다.", ""]
    for day in DAYS:
        rows = [e for _, e in topics if e["day"] == day]
        if not rows:
            continue
        lines.append(f"*{DAY_KO[day]}요일*")
        for e in rows:
            lines.append(f"   • {e['topic']} _({e['depth']})_")
    summary = "\n".join(lines)
    try:
        sys.path.insert(0, REPO_ROOT)
        import slack_blocks  # noqa: E402
        blocks = slack_blocks.topics_announce_blocks(summary, week=week)
    except Exception as e:  # noqa: BLE001 — 블록 실패해도 텍스트로라도 통보
        sys.stderr.write(f"[topics_tool] 주간 통보 블록 생성 실패({e}) — 텍스트 폴백\n")
        return cmd_notify(summary)
    ok = _post_blocks(f"다음 주({week}) 정기 주제 확정", blocks)
    return "주간 통보(버튼 포함) 게시" if ok else "주간 통보 실패"


def main(argv: list[str]) -> int:
    if not argv:
        sys.stderr.write(__doc__ or "")
        return 2
    cmd = argv[0]
    if cmd == "today":
        print(cmd_today())
    elif cmd == "list":
        print(cmd_list())
    elif cmd == "mark-done":
        if len(argv) < 2:
            sys.stderr.write("사용법: topics_tool.py mark-done \"<주제>\"\n")
            return 2
        ok = cmd_mark_done(argv[1])
        sys.stderr.write(("done 표시: " + argv[1] + "\n") if ok else ("일치 pending 없음: " + argv[1] + "\n"))
    elif cmd == "random-pool":
        print(cmd_random_pool())
    elif cmd == "apply-weekly":
        print(cmd_apply_weekly(argv[1]))
    elif cmd == "apply-replace-pending":
        print(cmd_apply_replace_pending(argv[1]))
    elif cmd == "notify":
        cmd_notify(argv[1] if len(argv) > 1 else "")
    elif cmd == "notify-weekly":
        print(cmd_notify_weekly())
    elif cmd == "roster":
        print(render_roster_md())
    else:
        sys.stderr.write(f"알 수 없는 명령: {cmd}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
