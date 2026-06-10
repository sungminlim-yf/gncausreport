#!/usr/bin/env python3
"""
이메일 발송 CLI — 보고서 마크다운 파일을 Resend 로 메일 발송.

bot/email_sender.py(모듈)를 명령행에서 바로 쓰기 위한 얇은 래퍼.
비밀정보(RESEND_API_KEY)·발신자·수신자는 .env 에서 읽는다(하드코딩 금지).

사용:
  # .env 의 EMAIL_RECIPIENTS 로 발송
  python scripts/send_email.py --file projects/brisbane-office/sample-report.md

  # 수신자·제목을 인라인 지정(.env 값보다 우선)
  python scripts/send_email.py \
      --file projects/brisbane-office/sample-report.md \
      --to sungmin.lim@youngfoods.com.au \
      --subject "브리즈번 CBD 오피스 시장 동향 (2026 1분기)"

제목 미지정 시 보고서 첫 H1(# 제목)을 제목으로 사용한다.
종료코드: 0 성공 / 1 실패.
"""
from __future__ import annotations

import argparse
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def load_env() -> None:
    """.env 를 가볍게 로드(이미 설정된 환경변수는 덮어쓰지 않음 — 인라인 변수 우선)."""
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


def first_h1(md: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return "시장 리포트"


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description="보고서 마크다운을 이메일로 발송")
    ap.add_argument("--file", required=True, help="보고서 마크다운 파일 경로")
    ap.add_argument("--subject", default="", help="메일 제목(미지정 시 보고서 첫 H1)")
    ap.add_argument("--to", default="", help="수신자(쉼표 구분). 지정 시 .env EMAIL_RECIPIENTS 보다 우선")
    args = ap.parse_args()

    if args.to:
        os.environ["EMAIL_RECIPIENTS"] = args.to  # 인라인 수신자 우선

    sys.path.insert(0, os.path.join(REPO_ROOT, "bot"))
    from email_sender import send_report_email, email_configured  # noqa: E402

    ok, reason = email_configured()
    if not ok:
        print(f"[send_email] 설정 부족: {reason}", file=sys.stderr)
        sys.exit(1)

    path = args.file if os.path.isabs(args.file) else os.path.join(REPO_ROOT, args.file)
    if not os.path.exists(path):
        print(f"[send_email] 파일 없음: {path}", file=sys.stderr)
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        md = f.read().strip()
    if not md:
        print("[send_email] 빈 파일 — 보낼 내용 없음.", file=sys.stderr)
        sys.exit(1)

    subject = args.subject or first_h1(md)
    result = send_report_email(subject, md)
    if result.get("ok"):
        print(f"[send_email] OK: {result.get('count')}명 발송 (id={result.get('id')}) — '{subject}'")
        sys.exit(0)
    print(f"[send_email] 실패: {result.get('detail')}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
