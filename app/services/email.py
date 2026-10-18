"""Pluggable email delivery.

The account-security flows (email verification, password reset) need to send a
message containing a secret link. Delivery is behind a tiny interface so the
transport can be swapped without touching the business logic:

* :class:`SmtpEmailService` — real delivery through ``smtplib`` (no extra
  dependency); used when ``EMAIL_BACKEND=smtp``.
* :class:`ConsoleEmailService` — **development only**: renders the message to a
  file inside ``EMAIL_OUTBOX_DIR`` instead of sending it, so a developer can
  follow the verification/reset link locally. It is refused outside local
  environments by :class:`~app.core.config.Settings`.
* :class:`DisabledEmailService` — drops messages (tests, or email switched off).

Design rules enforced here:

* the raw token never reaches a log line, an API response or an exception
  message; it only exists inside :class:`EmailMessage`, and
* a delivery failure is logged without its payload and never leaks the token.
"""

from __future__ import annotations

import hashlib
import logging
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage as _StdEmailMessage
from pathlib import Path
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EmailMessage:
    """A message handed to an :class:`EmailService`."""

    to: str
    subject: str
    body: str


class EmailService(Protocol):
    """Minimal transport contract."""

    def send(self, message: EmailMessage) -> None:  # pragma: no cover - protocol
        """Deliver ``message`` or raise."""
        ...


def redact_address(address: str) -> str:
    """Return a non-reversible hint of an address, safe for logs.

    ``alice@example.com`` becomes ``a***@example.com`` so operators can see that
    something was sent without writing the full address to disk.
    """
    local, _, domain = address.partition("@")
    if not local:
        return "***"
    return f"{local[0]}***@{domain}" if domain else f"{local[0]}***"


class DisabledEmailService:
    """Accepts messages and drops them (email delivery turned off)."""

    def send(self, message: EmailMessage) -> None:
        logger.warning(
            "Email delivery is disabled; dropped message for %s", redact_address(message.to)
        )


class ConsoleEmailService:
    """Development transport: writes the rendered message to a local outbox.

    The file *does* contain the secret link, which is the whole point of a
    development outbox; it lives in ``EMAIL_OUTBOX_DIR`` (git-ignored) and the
    backend cannot be enabled outside a local environment.
    """

    def __init__(self, outbox_dir: str) -> None:
        self.outbox_dir = Path(outbox_dir)

    def send(self, message: EmailMessage) -> None:
        self.outbox_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        # Name the file after a hash of the recipient, never after the token.
        digest = hashlib.sha256(message.to.encode("utf-8")).hexdigest()[:12]
        path = self.outbox_dir / f"{stamp}-{digest}.txt"
        rendered = f"To: {message.to}\nSubject: {message.subject}\n\n{message.body}\n"
        path.write_text(rendered, encoding="utf-8")
        logger.info("Development email for %s written to %s", redact_address(message.to), path)


class SmtpEmailService:
    """Production transport using the standard library's ``smtplib``."""

    def __init__(self, settings: Settings) -> None:
        if not settings.smtp_host:
            raise ValueError("smtp_host must be configured when email_backend='smtp'")
        self._settings = settings

    def send(self, message: EmailMessage) -> None:
        settings = self._settings
        payload = _StdEmailMessage()
        payload["From"] = settings.email_from
        payload["To"] = message.to
        payload["Subject"] = message.subject
        payload.set_content(message.body)

        context = ssl.create_default_context()
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as client:
            if settings.smtp_use_tls:
                client.starttls(context=context)
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password.get_secret_value())
            client.send_message(payload)


def build_email_service(settings: Settings) -> EmailService:
    """Return the transport selected by ``EMAIL_BACKEND``."""
    backend = settings.email_backend.lower()
    if backend == "smtp":
        return SmtpEmailService(settings)
    if backend == "console":
        return ConsoleEmailService(settings.email_outbox_dir)
    return DisabledEmailService()
