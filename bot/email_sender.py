#!/usr/bin/env python3
"""
이메일 발송 모듈 — Resend API (승인된 보고서를 지정 메일 리스트로 발송).

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
  EMAIL_RECIPIENTS  수신자 목록(쉼표 구분). 프라이버시 위해 BCC 로 발송(서로 주소 안 보임).
  EMAIL_SUBJECT_PREFIX  (선택) 제목 앞에 붙일 머리말. 예: "[GnC 시장리포트] "
"""
from __future__ import annotations

import html
import json
import os
import urllib.error
import urllib.request

RESEND_URL = "https://api.resend.com/emails"


def _recipients() -> list[str]:
    raw = os.environ.get("EMAIL_RECIPIENTS", "")
    return [a.strip() for a in raw.split(",") if a.strip()]


def email_configured() -> tuple[bool, str]:
    """발송 가능한 상태인지. (가능여부, 사유). 사유는 사람이 읽는 안내문."""
    if not os.environ.get("RESEND_API_KEY"):
        return False, "RESEND_API_KEY 가 .env 에 없습니다."
    if not os.environ.get("EMAIL_FROM"):
        return False, "EMAIL_FROM(발신자)이 .env 에 없습니다."
    if not _recipients():
        return False, "EMAIL_RECIPIENTS(수신자 목록)가 .env 에 없습니다."
    return True, ""


def markdown_to_html(md: str) -> str:
    """보고서 마크다운 → 이메일용 HTML(간단 스타일 래핑).

    markdown 패키지가 있으면 정식 변환, 없으면 깨지지 않게 <pre> 폴백.
    """
    try:
        import markdown as _md  # type: ignore
        body = _md.markdown(md, extensions=["extra", "sane_lists", "nl2br"])
    except Exception:
        body = "<pre style='white-space:pre-wrap;font-family:inherit'>" + html.escape(md) + "</pre>"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'></head>"
        "<body style='margin:0;padding:0;background:#f5f5f5'>"
        "<div style=\"max-width:680px;margin:0 auto;padding:28px 22px;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Noto Sans KR',sans-serif;"
        "font-size:15px;line-height:1.7;color:#222;background:#ffffff\">"
        f"{body}"
        "<hr style='border:none;border-top:1px solid #eee;margin:28px 0 14px'>"
        "<div style='font-size:12px;color:#999'>GnC AUS 시장 리포트 · 승인 후 자동 발송</div>"
        "</div></body></html>"
    )


def send_report_email(subject: str, markdown_body: str) -> dict:
    """보고서를 메일 리스트로 발송. 반환: {ok, count, id?, detail?}.

    수신자는 BCC(서로 주소 비공개). to 는 발신자 자신으로 둔다.
    예외를 던지지 않고 항상 dict 로 결과를 돌려준다(봇이 안 죽게).
    """
    ok, reason = email_configured()
    if not ok:
        return {"ok": False, "detail": reason, "count": 0}

    api_key = os.environ["RESEND_API_KEY"]
    sender = os.environ["EMAIL_FROM"]
    recipients = _recipients()
    prefix = os.environ.get("EMAIL_SUBJECT_PREFIX", "")
    full_subject = f"{prefix}{subject}".strip()

    payload = {
        "from": sender,
        "to": [sender],          # 발신자 자신(수신자는 BCC)
        "bcc": recipients,       # 실제 수신자 — 서로의 주소가 보이지 않음
        "subject": full_subject or "(제목 없음)",
        "html": markdown_to_html(markdown_body),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        RESEND_URL, data=data, method="POST",
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
            body = json.loads(raw) if raw else {}
            return {"ok": True, "count": len(recipients), "id": body.get("id")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        # Resend 오류는 보통 {"message": "...", "name": "..."} JSON
        try:
            detail = json.loads(detail).get("message", detail)
        except Exception:
            pass
        return {"ok": False, "detail": f"Resend {e.code}: {detail}", "count": 0}
    except urllib.error.URLError as e:
        return {"ok": False, "detail": f"네트워크 오류: {e.reason}", "count": 0}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": f"발송 실패: {e}", "count": 0}
