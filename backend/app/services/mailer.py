"""Pluggable transactional email.

Console backend by default (logs the message so a developer can copy links);
SMTP backend when ``SMTP_HOST`` is configured. Never raises to the caller — a
mail failure must not break the request that triggered it.

Production SMTP env vars:
    SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
    SMTP_FROM (default = SMTP_USER), SMTP_TLS (default true)
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def send_email(to: str, subject: str, body: str) -> bool:
    """Send a plaintext email. Returns True on success, False otherwise."""
    host = _env("SMTP_HOST")
    if not host:
        # No SMTP configured: log so a developer can act on it in dev.
        logger.info("[email:console] to=%s subject=%s\n%s", to, subject, body)
        return True
    try:
        port = int(_env("SMTP_PORT", "587") or "587")
    except ValueError:
        port = 587
    user = _env("SMTP_USER")
    password = _env("SMTP_PASSWORD")
    sender = _env("SMTP_FROM", user or "no-reply@localhost")
    use_tls = _env("SMTP_TLS", "true").lower() in {"1", "true", "yes", "on"}

    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=15) as server:
            if use_tls:
                server.starttls()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
        logger.info("[email:smtp] sent to=%s subject=%s", to, subject)
        return True
    except Exception as exc:  # never break the request because mail failed
        logger.warning("[email:smtp] send failed to=%s: %s", to, exc)
        return False


__all__ = ["send_email"]
