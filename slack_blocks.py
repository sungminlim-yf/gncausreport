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
  본문 단락   → section(mrkdwn: *굵게*·<url|텍스트>). 한 문장=한 줄로 끊고 각 줄 앞에 • 닷포인트(가독성)
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
# 문장 종결 마침표 뒤 줄바꿈(가독성): 한글 음절·')'·']' 뒤의 '. '만 분리.
# → 소수점(6.27, 공백 없음)·번호목록(1. , 숫자 뒤)·약어(No. 1, 라틴 글자 뒤)는 건드리지 않음.
_SENT_END = re.compile(r"(?<=[가-힣\)\]])\.[ \t]+")

# 본문 문단 들여쓰기 — 헤더 아래 종속 문단임을 시각화(2칸). 닷·화살표·번호목록 등 본문 줄 전체에 적용.
_INDENT = "  "
_DOT = _INDENT + "• "   # 닷포인트(들여쓰기 + 불릿). 본문 문장·불릿 공용.
# 닷을 붙이지 않는 '마커 줄': 화살표(시사점) · 동그라미숫자(사례) 로 시작.
_ARROWS = ("→", "⇒", "➔", "➜", "▶", "►", "↳", "⤷", "=>")
_CIRCLED = set("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")
_NUMHEAD = re.compile(r"^\d+\.\s")          # 번호 헤더·목록(1. , 2. …)
# 본문 인용 [1]/[12] → 위첨자 ⁽¹⁾(작게, 읽기 흐름 방해 최소화). 출처 목록의 [n] 은 유지.
_CITE = re.compile(r"\[(\d+)\]")
_SUP = {"0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
        "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹"}
# 출처 줄에서 '[n] … URL' → '[n] URL'(제목·설명 생략, 링크만).
_SRC_TAG = re.compile(r"^\s*(\[\d+\])")
_URL = re.compile(r"https?://\S+")

SECTION_LIMIT = 2900   # section/context mrkdwn 안전 한도(<3000)
HEADER_LIMIT = 150
MAX_BLOCKS = 45        # 메시지당 블록 안전 한도(<50)


def to_mrkdwn(text: str) -> str:
    """마크다운 인라인 → 슬랙 mrkdwn. [t](u)→<u|t>, **굵게**→*굵게*, '- '→' • '(1칸 들여쓰기)."""
    text = _LINK.sub(r"<\2|\1>", text)
    text = _BOLD.sub(r"*\1*", text)
    out = []
    for ln in text.split("\n"):
        m = re.match(r"^(\s*)-\s+(.*)", ln)
        if m:
            ln = m.group(1) + _DOT + m.group(2)
        out.append(ln)
    return "\n".join(out)


def _plain(text: str) -> str:
    """header용 평문 — 마크다운 강조 기호 제거."""
    return text.replace("**", "").replace("*", "").strip()


def _sentence_breaks(text: str) -> str:
    """문장 종결 마침표 뒤에 줄바꿈을 넣어 '한 문장 = 한 줄' 가독성 향상.
    소수점·번호목록·약어(No.)는 보존(_SENT_END 가 한글/괄호/대괄호 뒤만 매칭)."""
    return _SENT_END.sub(".\n", text)


def _small_citations(text: str) -> str:
    """본문 인용 [1]/[12] → 위첨자 ⁽¹⁾(작게). 출처 목록에는 적용하지 않는다."""
    return _CITE.sub(lambda m: "⁽" + "".join(_SUP[d] for d in m.group(1)) + "⁾", text)


def _simplify_source(line: str) -> str:
    """출처 줄 '[n] 제목 — 설명 (날짜) URL' → '[n] URL'(링크만, 제목 생략)."""
    tag = _SRC_TAG.match(line)
    url = _URL.search(line)
    return f"{tag.group(1)} {url.group(0)}" if tag and url else line


def _dot_points(text: str) -> str:
    """본문 문단을 들여쓰기해 '헤더 아래 딸린 문단'임을 시각화하고, 일반 문장엔 닷(•)을 붙인다.

    - base 유지(들여쓰기 X): 본문 내 굵은 번호 소제목('*1. …*')·블록인용('>'). (### / ## 섹션 제목은 flush_body 미경유)
    - 들여쓰기만(닷 X): 화살표(→ 시사점)·동그라미숫자(① 사례)·번호 목록('1. …').
    - 들여쓰기 + 닷: 그 외 일반 문장·기존 불릿.
    문단의 첫 줄뿐 아니라 모든 줄을 동일 들여쓰기해 헤더와 본문이 구별되게 한다."""
    out = []
    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            out.append("")
            continue
        starred = s.startswith("*")
        core = s[1:].lstrip() if starred else s  # 굵게(*..)로 시작하면 안쪽을 기준으로 판정
        if (starred and _NUMHEAD.match(core)) or s.startswith(">"):
            out.append(s)                                   # 헤더·블록인용 → base
        elif s.startswith("•"):
            out.append(_DOT + s[1:].lstrip())               # 기존 불릿 → 들여쓰기 정규화
        elif s.startswith(_ARROWS) or s[:1] in _CIRCLED or _NUMHEAD.match(s):
            out.append(_INDENT + s)                         # 화살표·동그라미·번호목록 → 들여쓰기만
        else:
            out.append(_DOT + s)                            # 일반 문장 → 들여쓰기 + 닷
    return "\n".join(out)


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
        # 순서 주의: 문장분리 → 인용 위첨자화 → 닷포인트.
        # (위첨자화를 먼저 하면 ']' 가 '⁾' 로 바뀌어 '[n]. ' 뒤 문장분리가 안 됨)
        # strip('\n') 만: 첫 줄의 닷 들여쓰기(맨 앞 공백)를 보존(.strip() 은 그걸 먹음)
        txt = to_mrkdwn("\n".join(para)).strip("\n")
        txt = _dot_points(_small_citations(_sentence_breaks(txt)))
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
        simplified = [_simplify_source(ln) for ln in src]   # 제목 생략, [n] URL 만
        body = "*📎 출처*\n" + to_mrkdwn("\n".join(simplified))
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
