"""Sending one magic link. SMTP, reusing the pattern me_app already runs.

NO NEW VENDOR. `collector_watchdog.py` on the other box already sends over SMTP
with an app password (ALERT_SMTP_HOST / USER / PASS), so this needs no account
to open and no key to rotate. The variables are named separately here so the
two can be split later without touching code - point them at the same values
today.

WORTH KNOWING BEFORE THIS SCALES. An app-password relay is fine for a handful of
sign-ins a day and is not a transactional mail service: there is no bounce
handling, no suppression list, no delivery telemetry, and consumer providers
throttle on volume and will eventually treat a sudden burst as suspicious. The
day this sends more than a few dozen links a day, move it to a real
transactional sender. That is a swap of this one function.
"""
import os
import smtplib
import ssl
from email.message import EmailMessage


def _cfg(name: str, fallback: str, default: str = "") -> str:
    return os.environ.get(name) or os.environ.get(fallback) or default


def send_magic_link(to: str, link: str, minutes: int) -> None:
    host = _cfg("PUBLIC_SMTP_HOST", "ALERT_SMTP_HOST")
    port = int(_cfg("PUBLIC_SMTP_PORT", "ALERT_SMTP_PORT", "587"))
    user = _cfg("PUBLIC_SMTP_USER", "ALERT_SMTP_USER")
    pw = _cfg("PUBLIC_SMTP_PASS", "ALERT_SMTP_PASS")
    sender = _cfg("MAIL_FROM", "PUBLIC_SMTP_USER", user)
    if not (host and user and pw):
        raise RuntimeError("SMTP is not configured")

    m = EmailMessage()
    m["Subject"] = "Your Morning Execution sign-in link"
    m["From"] = sender
    m["To"] = to
    # PLAIN TEXT, no HTML part. A one-line transactional mail gains nothing from
    # markup and loses on spam scoring and on clients that render it badly.
    m.set_content(
        "Here is your sign-in link for the Morning Execution tools.\n\n"
        "%s\n\n"
        "It works once and expires in %d minutes.\n\n"
        "If you did not ask for this, ignore it - nothing has changed on your "
        "account, and the link cannot be used to alter it.\n"
        % (link, minutes))

    ctx = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=ctx, timeout=20) as s:
            s.login(user, pw)
            s.send_message(m)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=ctx)
            s.login(user, pw)
            s.send_message(m)
