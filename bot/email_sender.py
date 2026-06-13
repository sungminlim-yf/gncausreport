#!/usr/bin/env python3
"""
이메일 발송 모듈 — Resend API (승인된 보고서를 지정 메일 리스트로 발송).

두 가지 발송 경로(.env 의 RESEND_AUDIENCE_ID 유무로 자동 선택):
  1) 오디언스(Broadcasts) 경로 — RESEND_AUDIENCE_ID 가 있으면 사용.
     수신자 목록을 Resend '오디언스(Audience)'가 관리하고, 본문에 {{{RESEND_UNSUBSCRIBE_URL}}}
     을 넣어 **Resend 가 호스팅하는 1클릭 수신거부**를 제공한다(수신거부자는 다음 발송에서 자동 제외).
     → 수신거부가 자동화되고, 명단은 Resend 대시보드/CLI(아래 main)로 관리한다.
  2) BCC 경로(폴백) — RESEND_AUDIENCE_ID 가 없으면 기존처럼 EMAIL_RECIPIENTS 를 BCC 로 발송.
     서로 주소가 안 보이는 단순 발송. 수신거부 자동화는 없음.

설계 의도(나중에 발신자 갈아끼우기 쉽게):
  - 발신자·수신자·API키를 코드에 박지 않고 전부 .env 로만 둔다.
    회사 도메인 이메일이 정해지면 EMAIL_FROM 한 줄만 바꾸면 끝.
  - Resend 는 '도메인 인증(youngfoods.com.au)'을 한 번 해두면 그 도메인의
    어떤 주소로든(reports@ / info@ / noreply@) 발신 가능 → 주소 교체가 설정 한 줄.
  - 외부 의존성 최소화: HTTP 호출은 표준 라이브러리(urllib)로. (마크다운→HTML 만 markdown 패키지)

환경변수(.env):
  RESEND_API_KEY    re_... (필수)
  EMAIL_FROM        발신자. "표시이름 <주소>" 또는 "주소" (필수)
                    도메인 인증 전 테스트: onboarding@resend.dev (본인 가입 이메일로만 발송됨)
                    인증 후: "GnC AUS Report <reports@youngfoods.com.au>" 로 교체
                    ※ Broadcasts(오디언스 경로)는 onboarding@resend.dev 발신 불가 → 도메인 인증 필요.
  EMAIL_RECIPIENTS  (BCC 경로) 수신자 목록(쉼표 구분). BCC 로 발송(서로 주소 안 보임).
  RESEND_AUDIENCE_ID (오디언스 경로) Resend 오디언스 ID. 있으면 Broadcasts 로 발송 + 자동 수신거부.
  EMAIL_SUBJECT_PREFIX  (선택) 제목 앞에 붙일 머리말. 예: "[GnC 시장리포트] "

운영자 명단 관리 CLI(오디언스 경로):
  python bot/email_sender.py create-audience "GnC 시장리포트 구독자"   새 오디언스 생성 → ID 출력(.env 에 기입)
  python bot/email_sender.py import-recipients                       EMAIL_RECIPIENTS 를 오디언스 컨택트로 일괄 등록
  python bot/email_sender.py add-contact <email> [이름] [성]          컨택트 1건 추가(자율 구독 요청 수동 반영)
  python bot/email_sender.py list-contacts                           오디언스 컨택트·수신거부 상태 목록
"""
from __future__ import annotations

import html
import json
import os
import sys
import urllib.error
import urllib.request

RESEND_BASE = "https://api.resend.com"
RESEND_URL = RESEND_BASE + "/emails"


def _recipients() -> list[str]:
    raw = os.environ.get("EMAIL_RECIPIENTS", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def _audience_id() -> str:
    return os.environ.get("RESEND_AUDIENCE_ID", "").strip()


def email_configured() -> tuple[bool, str]:
    """발송 가능한 상태인지. (가능여부, 사유). 사유는 사람이 읽는 안내문."""
    if not os.environ.get("RESEND_API_KEY"):
        return False, "RESEND_API_KEY 가 .env 에 없습니다."
    if not os.environ.get("EMAIL_FROM"):
        return False, "EMAIL_FROM(발신자)이 .env 에 없습니다."
    # 오디언스 경로면 수신자는 Resend 가 관리하므로 EMAIL_RECIPIENTS 불필요.
    if not _audience_id() and not _recipients():
        return False, "EMAIL_RECIPIENTS(수신자 목록) 또는 RESEND_AUDIENCE_ID 중 하나가 .env 에 필요합니다."
    return True, ""


# ── HTTP 헬퍼(표준 라이브러리) ──────────────────────────────────────────
def _request(method: str, path: str, payload: dict | None = None) -> tuple[int, dict]:
    """Resend API 호출. 반환 (status, json). 예외 없이 (0, {error}) 로 폴백."""
    api_key = os.environ.get("RESEND_API_KEY", "")
    url = path if path.startswith("http") else RESEND_BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            # User-Agent 필수: 없으면 Resend 앞단 Cloudflare 가 기본 파이썬 UA 를 차단(403/1010).
            "User-Agent": "gncausreport-bot/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        try:
            detail = json.loads(detail)
        except Exception:
            detail = {"message": detail}
        return e.code, detail if isinstance(detail, dict) else {"message": str(detail)}
    except urllib.error.URLError as e:
        return 0, {"message": f"네트워크 오류: {e.reason}"}
    except Exception as e:  # noqa: BLE001
        return 0, {"message": f"요청 실패: {e}"}


# ── 마크다운 → HTML ─────────────────────────────────────────────────────
def markdown_to_html(md: str, *, unsubscribe: bool = False, extra_html: str = "") -> str:
    """보고서 마크다운 → 이메일용 HTML(간단 스타일 래핑).

    markdown 패키지가 있으면 정식 변환, 없으면 깨지지 않게 <pre> 폴백.
    extra_html: 본문과 푸터 사이에 그대로 삽입할 HTML(예: 진행현황 로드맵 카드 — 마크다운 변환 없이).
    unsubscribe=True 면 푸터에 Resend 수신거부 링크 자리표시자({{{RESEND_UNSUBSCRIBE_URL}}})를 넣는다
    (Broadcasts 경로에서 Resend 가 실제 URL 로 치환·호스팅).
    """
    try:
        import markdown as _md  # type: ignore
        body = _md.markdown(md, extensions=["extra", "sane_lists", "nl2br"])
    except Exception:
        body = "<pre style='white-space:pre-wrap;font-family:inherit'>" + html.escape(md) + "</pre>"
    # 수신거부 줄은 f-string 밖에서 합친다(삼중 중괄호 자리표시자 보존).
    # 과하지 않게, 옅은 회색 텍스트링크보다 한 단계 또렷한 '아웃라인 pill' 버튼으로 노출.
    unsub = ""
    if unsubscribe:
        unsub = (
            "<div style='margin-top:12px'>"
            "<a href=\"{{{RESEND_UNSUBSCRIBE_URL}}}\" "
            "style='display:inline-block;padding:4px 13px;border:1px solid #b9c6dd;"
            "border-radius:6px;background:#f2f6fc;color:#3f5e8c;text-decoration:none;"
            "font-size:13px;font-weight:600'>수신거부</a>"
            "<div style='margin-top:7px;font-size:12px;color:#999'>수신 추가 요청은 본 메일에 회신</div>"
            "</div>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'></head>"
        "<body style='margin:0;padding:0;background:#f5f5f5'>"
        "<div style=\"max-width:680px;margin:0 auto;padding:28px 22px;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans KR',sans-serif;"
        "font-size:15px;line-height:1.7;color:#222;background:#ffffff\">"
        + body
        + extra_html
        + "<hr style='border:none;border-top:1px solid #eee;margin:28px 0 14px'>"
        + "<div style='font-size:12px;color:#999'>GnC AUS 시장 리포트 · 승인 후 자동 발송"
        + unsub
        + "</div>"
        + "</div></body></html>"
    )


# ── 발송 ────────────────────────────────────────────────────────────────
def send_report_email(subject: str, markdown_body: str, extra_html: str = "") -> dict:
    """보고서를 메일 리스트로 발송. 반환: {ok, count, id?, detail?}.

    RESEND_AUDIENCE_ID 가 있으면 Broadcasts(오디언스 + 자동 수신거부), 없으면 BCC.
    extra_html: 본문 뒤에 붙일 HTML(진행현황 로드맵 카드 등). 예외를 던지지 않고 dict 반환.
    """
    ok, reason = email_configured()
    if not ok:
        return {"ok": False, "detail": reason, "count": 0}

    # 접두어와 제목 사이 한 칸 공백 보장(예: "[GnC 시장리포트] 제목"). 접두어 없으면 제목만.
    prefix = os.environ.get("EMAIL_SUBJECT_PREFIX", "").strip()
    full_subject = (f"{prefix} {subject}" if prefix else subject).strip() or "(제목 없음)"
    sender = os.environ["EMAIL_FROM"]

    if _audience_id():
        return _send_broadcast(
            sender, full_subject,
            markdown_to_html(markdown_body, unsubscribe=True, extra_html=extra_html))
    return _send_bcc(sender, full_subject, markdown_to_html(markdown_body, extra_html=extra_html))


def _send_bcc(sender: str, full_subject: str, html_body: str) -> dict:
    """폴백 경로: EMAIL_RECIPIENTS 를 BCC 로 단건 발송(서로 주소 비공개)."""
    recipients = _recipients()
    # To 는 '실제로 받을 수 있는' 주소여야 한다. 발송전용 noreply@ 를 To 로 쓰면
    # 하드 바운스→Resend 억제목록 등재→이후 모든 메일이 suppressed 되는 함정에 빠진다.
    to_addr = os.environ.get("EMAIL_TO", "").strip() or (recipients[0] if recipients else sender)
    payload = {
        "from": sender,
        "to": [to_addr],         # 실제 수신 가능한 주소(수신자 목록은 BCC)
        "bcc": recipients,       # 실제 수신자 — 서로의 주소가 보이지 않음
        "subject": full_subject,
        "html": html_body,
    }
    status, body = _request("POST", "/emails", payload)
    if status and 200 <= status < 300:
        return {"ok": True, "count": len(recipients), "id": body.get("id")}
    return {"ok": False, "detail": _fmt_err(status, body), "count": 0}


def _send_broadcast(sender: str, full_subject: str, html_body: str) -> dict:
    """오디언스 경로: Broadcast 생성 → 발송. Resend 가 수신거부를 호스팅·관리."""
    aud = _audience_id()
    create = {"audience_id": aud, "from": sender, "subject": full_subject, "html": html_body}
    status, body = _request("POST", "/broadcasts", create)
    if not (status and 200 <= status < 300) or not body.get("id"):
        return {"ok": False, "detail": "브로드캐스트 생성 실패: " + _fmt_err(status, body), "count": 0}
    bid = body["id"]
    s2, b2 = _request("POST", f"/broadcasts/{bid}/send", {})
    if not (s2 and 200 <= s2 < 300):
        return {"ok": False, "detail": "브로드캐스트 발송 실패: " + _fmt_err(s2, b2), "count": 0}
    return {"ok": True, "count": _audience_active_count(), "id": bid}


def _fmt_err(status: int, body: dict) -> str:
    msg = body.get("message") or body.get("error") or json.dumps(body, ensure_ascii=False)[:200]
    return f"Resend {status}: {msg}" if status else str(msg)


# ── 오디언스/컨택트 관리(운영자 CLI 용) ──────────────────────────────────
def _audience_contacts() -> list[dict]:
    aud = _audience_id()
    if not aud:
        return []
    status, body = _request("GET", f"/audiences/{aud}/contacts")
    if status and 200 <= status < 300:
        return body.get("data", []) or []
    return []


def _audience_active_count() -> int | None:
    """수신거부하지 않은 컨택트 수(발송 대상). 조회 실패 시 None."""
    contacts = _audience_contacts()
    if not contacts:
        return None
    return sum(1 for c in contacts if not c.get("unsubscribed"))


def create_audience(name: str) -> dict:
    return _request("POST", "/audiences", {"name": name})[1]


def add_contact(email: str, first_name: str = "", last_name: str = "") -> tuple[int, dict]:
    aud = _audience_id()
    if not aud:
        return 0, {"message": "RESEND_AUDIENCE_ID 미설정"}
    payload = {"email": email, "unsubscribed": False}
    if first_name:
        payload["first_name"] = first_name
    if last_name:
        payload["last_name"] = last_name
    return _request("POST", f"/audiences/{aud}/contacts", payload)


def _cli(argv: list[str]) -> int:
    # .env 로드(이 모듈을 직접 실행할 때) — 봇/스크립트 경유 시엔 이미 로드돼 있음.
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    env_path = os.path.join(repo, ".env")
    if os.path.exists(env_path):
        for line in open(env_path, "r", encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    if not argv:
        sys.stderr.write(__doc__ or "")
        return 2
    cmd = argv[0]
    if cmd == "create-audience":
        name = argv[1] if len(argv) > 1 else "GnC 시장리포트 구독자"
        print(json.dumps(create_audience(name), ensure_ascii=False, indent=2))
        print("→ 위 id 를 .env 의 RESEND_AUDIENCE_ID 에 기입하세요.", file=sys.stderr)
    elif cmd == "import-recipients":
        if not _audience_id():
            sys.stderr.write("RESEND_AUDIENCE_ID 미설정 — 먼저 create-audience 후 .env 기입\n")
            return 2
        recs = _recipients()
        if not recs:
            sys.stderr.write("EMAIL_RECIPIENTS 가 비어 있음\n")
            return 2
        for e in recs:
            st, body = add_contact(e)
            print(f"{'✓' if st and 200 <= st < 300 else '✗'} {e}: {body.get('id') or _fmt_err(st, body)}")
    elif cmd == "add-contact":
        if len(argv) < 2:
            sys.stderr.write("사용법: add-contact <email> [이름] [성]\n")
            return 2
        st, body = add_contact(argv[1], argv[2] if len(argv) > 2 else "", argv[3] if len(argv) > 3 else "")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return 0 if st and 200 <= st < 300 else 1
    elif cmd == "list-contacts":
        for c in _audience_contacts():
            flag = "🚫수신거부" if c.get("unsubscribed") else "✅구독중"
            print(f"{flag}  {c.get('email')}  {c.get('first_name','')} {c.get('last_name','')}".rstrip())
    else:
        sys.stderr.write(f"알 수 없는 명령: {cmd}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
