from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required env var: {name}")
    return value


def main() -> int:
    subject = os.getenv("ALERT_SUBJECT", "DK NCAAB Alert")
    body = os.getenv("ALERT_BODY", "No message body provided")

    smtp_host = _required("ALERT_SMTP_HOST")
    smtp_port = int(os.getenv("ALERT_SMTP_PORT", "587"))
    smtp_user = _required("ALERT_SMTP_USER")
    smtp_pass = _required("ALERT_SMTP_PASS")
    from_email = os.getenv("ALERT_FROM_EMAIL", smtp_user)
    to_email = os.getenv("ALERT_TO_EMAIL", "nonemakerc05@gmail.com")

    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.starttls(context=context)
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    print(f"Alert email sent to {to_email}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Alert email failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
