#!/usr/bin/env python3
"""
보고서 마크다운 → 슬랙 Block Kit 변환 (가독성 개선).

슬랙은 일반 마크다운을 렌더링하지 않는다(# 헤더·** 굵게·[t](u) 링크 미지원).
이 모듈은 content-production 양식(# 제목 → ## 섹션 → 본문 → 📎 출처 → 면책)을
Block Kit 블록으로 변환한다. send.py(스테이징 초안)와 bot/server.py(본 게시)가 공유한다.

매핑:
  # 제목      → header 블록(큰 굵은 글씨)
  ## 섹션     → divider + section(*굵게*) 블록
  ### 소제목  → section(*굵게*)
  본문 단락   → section(mrkdwn: *굵게*·<url|텍스트>·• 불릿)
  ---         → divider
  📎 출처     → context(작은 회색) 블록
  꼬리 면책   → context 블록

슬랙 제약 대응: header ≤150자, section/context 텍스트 ≤3000자, 메시지당 블록 ≤50.
표준 라이브러리만 사용(외부 의존성 없음).
"""
from __future__ import annotations

import re

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")

SECTION_LIMIT = 2900   # section/context mrkdwn 안전 한도(<3000)
HEADER_LIMIT = 150
MAX_BLOCKS = 45        # 메시지당 블록 안전 한도(<50)


def to_mrkdwn(text: str) -> str:
    """마크다운 인라인 → 슬랙 mrkdwn. [t](u)→<u|t>, **굵게**→*굵게*, '- '→'• '."""
    text = _LINK.sub(r"<\2|\1>", text)
    text = _BOLD.sub(r"*\1*", text)
    out = []
    for ln in text.split("\n"):
        m = re.match(r"^(\s*)-\s+(.*)", ln)
        if m:
            ln = m.group(1) + "• " + m.group(2)
        out.append(ln)
    return "\n".join(out)


def _plain(text: str) -> str:
    """header용 평문 — 마크다운 강조 기호 제거."""
    return text.replace("**", "").replace("*", "").strip()


def _split_text(text: str, limit: int = SECTION_LIMIT) -> list[str]:
    """긴 텍스트를 줄 경계로 limit 이하 조각으로 분할."""
    if len(text) <= limit:
        return [text]
    out, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > limit and buf:
            out.append(buf.rstrip())
            buf = ""
        buf += line + "\n"
    if buf.strip():
        out.append(buf.rstrip())
    return out


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": _plain(text)[:HEADER_LIMIT], "emoji": True}}


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text[:3000]}]}


def _divider() -> dict:
    return {"type": "divider"}


def render_blocks(report: str) -> list[dict]:
    """보고서 마크다운 전체를 Block Kit 블록 리스트로 변환."""
    blocks: list[dict] = []
    para: list[str] = []
    src: list[str] = []
    foot: list[str] = []
    mode = "body"  # body | sources | footer

    def flush_body() -> None:
        if not para:
            return
        txt = to_mrkdwn("\n".join(para)).strip()
        para.clear()
        for piece in _split_text(txt):
            if piece.strip():
                blocks.append(_section(piece))

    for raw in report.splitlines():
        st = raw.strip()
        if st.startswith("# ") and not st.startswith("## "):
            flush_body()
            blocks.append(_header(st[2:]))
            mode = "body"
        elif st.startswith("## "):
            title = st[3:].strip()
            flush_body()
            if "출처" in title or "📎" in title:
                mode = "sources"
                continue
            blocks.append(_divider())
            if title not in ("본문", "Body"):   # 구조용 라벨은 구분선만, 군더더기 제거
                blocks.append(_section("*" + to_mrkdwn(title) + "*"))
            mode = "body"
        elif st.startswith("### "):
            flush_body()
            blocks.append(_section("*" + to_mrkdwn(st[4:]) + "*"))
            mode = "body"
        elif st == "---":
            if mode == "sources":
                mode = "footer"   # 출처 끝, 이후는 꼬리 면책
            else:
                flush_body()
                blocks.append(_divider())
        else:
            if mode == "sources":
                if st:
                    src.append(raw.rstrip())
            elif mode == "footer":
                if st:
                    foot.append(st)
            elif st == "":
                flush_body()
            else:
                para.append(raw.rstrip())

    flush_body()
    if src:
        blocks.append(_divider())
        body = "*📎 출처*\n" + to_mrkdwn("\n".join(src))
        for piece in _split_text(body):
            blocks.append(_context(piece))
    if foot:
        blocks.append(_context(_plain(to_mrkdwn(" ".join(foot)))))
    return blocks


def chunk_blocks(blocks: list[dict], max_blocks: int = MAX_BLOCKS) -> list[list[dict]]:
    """블록 리스트를 메시지당 max_blocks 이하 그룹으로 분할(보통 1그룹)."""
    if not blocks:
        return [[]]
    return [blocks[i:i + max_blocks] for i in range(0, len(blocks), max_blocks)]


def fallback_text(report: str) -> str:
    """blocks 사용 시 알림/접근성용 top-level text(제목 또는 첫 줄)."""
    for raw in report.splitlines():
        st = raw.strip()
        if st.startswith("# "):
            return _plain(st[2:])[:200]
    for raw in report.splitlines():
        if raw.strip():
            return _plain(raw.strip())[:200]
    return "보고서"


def approval_action_blocks(run_id: str) -> list[dict]:
    """초안 메시지 끝에 붙일 승인/반려 버튼 블록(D5)."""
    return [
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": f"run-id: `{run_id}` · 지정 승인자만 유효(D17)"}]},
        {"type": "actions", "block_id": "approval_actions", "elements": [
            {"type": "button", "style": "primary", "text": {"type": "plain_text", "text": "✅ 승인"},
             "action_id": "approve_post", "value": run_id},
            {"type": "button", "style": "danger", "text": {"type": "plain_text", "text": "❌ 반려"},
             "action_id": "reject_post", "value": run_id},
        ]},
    ]
