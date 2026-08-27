"""Email sending (WP-44) - config/notifications.yaml's ``email`` block plus
env credential overrides, sent through smtplib with an injectable transport
so no test ever opens a real socket. Practical pub-sub without a broker:
``Mailer.send`` moves bytes; ``notify_immediate`` (below) and
``src.notifications.digest`` decide who gets them and when.
"""

import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Optional

import yaml

from ..storage.notifications import (
    NotificationStateStore, NotificationSubscriptionsStore, parse_naive_utc,
)

logger = logging.getLogger(__name__)

# Per config/notifications.yaml's own comments: username/password may be
# set via these instead of the yaml file, and win over it when set.
ENV_SMTP_USERNAME = "POLICYSEARCH__NOTIFICATIONS__SMTP_USERNAME"
ENV_SMTP_PASSWORD = "POLICYSEARCH__NOTIFICATIONS__SMTP_PASSWORD"

# One send per topic per hour (WP-44) - bounds how often a burst of sweep/
# scan failures can reach a subscriber's inbox.
IMMEDIATE_RATE_LIMIT = timedelta(hours=1)


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_email_config(config_dir: str = "config") -> dict:
    """The ``email:`` block of config/notifications.yaml, with
    smtp_username/smtp_password overridden from the environment when set.
    Tolerates a missing file (an install that never touched
    notifications.yaml just looks unconfigured).
    """
    data = _load_yaml(Path(config_dir) / "notifications.yaml").get("email", {})
    return {
        "enabled": bool(data.get("enabled", False)),
        "smtp_host": data.get("smtp_host") or "",
        "smtp_port": int(data.get("smtp_port", 587)),
        "smtp_use_tls": bool(data.get("smtp_use_tls", True)),
        "smtp_username": os.environ.get(ENV_SMTP_USERNAME) or data.get("smtp_username") or "",
        "smtp_password": os.environ.get(ENV_SMTP_PASSWORD) or data.get("smtp_password") or "",
        "from_email": data.get("from_email") or "",
    }


def smtp_configured(config: dict) -> bool:
    """Whether ``config`` (load_email_config's shape) has everything a real
    send needs: enabled, plus host/username/password/from_email present."""
    return bool(
        config.get("enabled")
        and config.get("smtp_host")
        and config.get("smtp_username")
        and config.get("smtp_password")
        and config.get("from_email")
    )


class Mailer:
    """Loads config/notifications.yaml once and sends through
    ``smtplib.SMTP`` (or an injected stand-in) via STARTTLS.

    ``transport`` is anything callable as ``transport(host, port,
    timeout=30)`` returning a context manager exposing ``.starttls()``/
    ``.login()``/``.send_message()`` - the exact shape of ``smtplib.SMTP``
    itself, which is the default. Tests inject a fake with that shape so no
    test ever opens a real socket.
    """

    def __init__(
        self,
        config_dir: str = "config",
        data_dir: str = "data",
        transport=smtplib.SMTP,
    ):
        self._config = load_email_config(config_dir)
        self._state = NotificationStateStore(data_dir=data_dir)
        self._transport = transport

    @property
    def smtp_configured(self) -> bool:
        return smtp_configured(self._config)

    def send(self, recipients: list[str], subject: str, body_text: str) -> bool:
        """Send one email to ``recipients``. Returns whether it went out.

        Never raises: ``smtplib.SMTPException``/``OSError`` (a bad host,
        refused auth, a dropped connection) is caught, logged, and recorded
        to the kv state as the last send error - callers (digests,
        immediate alerts) must keep running whether or not the email
        actually left.
        """
        if not self.smtp_configured or not recipients:
            return False

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._config["from_email"]
        msg["To"] = ", ".join(recipients)
        msg.set_content(body_text)

        try:
            with self._transport(
                self._config["smtp_host"], self._config["smtp_port"], timeout=30,
            ) as server:
                if self._config["smtp_use_tls"]:
                    server.starttls(context=ssl.create_default_context())
                server.login(self._config["smtp_username"], self._config["smtp_password"])
                server.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as e:
            logger.warning("Email send failed: %s", e)
            self._state.record_send_error(str(e))
            return False


def notify_immediate(
    topic: str,
    subject: str,
    body: str,
    data_dir: str = "data",
    config_dir: str = "config",
    now: Optional[datetime] = None,
    transport=smtplib.SMTP,
) -> None:
    """Send ``subject``/``body`` to every "immediate"-frequency subscriber
    of ``topic`` right now - the failure/budget-stop path the news sweep
    and scan manager call (see their call sites for the exact trigger).

    Never raises. Silently (logged) skips when there are no "immediate"
    subscribers for ``topic``, when SMTP is not configured, or when the
    topic already sent within the last hour (IMMEDIATE_RATE_LIMIT) - a
    burst of failures must not spam a subscriber's inbox once per failure.

    ``transport`` is forwarded to the ``Mailer`` this constructs - see
    ``Mailer``'s docstring; tests inject a fake so this never opens a real
    socket.
    """
    now = now or datetime.utcnow()
    state = NotificationStateStore(data_dir=data_dir)

    last_sent = state.get_immediate_last_sent(topic)
    if last_sent is not None and now - parse_naive_utc(last_sent) < IMMEDIATE_RATE_LIMIT:
        logger.info("Immediate notification for topic %r rate-limited", topic)
        return

    recipients = [
        s["email"] for s in NotificationSubscriptionsStore(data_dir=data_dir).list()
        if s["frequency"] == "immediate" and topic in s["topics"]
    ]
    if not recipients:
        return

    mailer = Mailer(config_dir=config_dir, data_dir=data_dir, transport=transport)
    if not mailer.smtp_configured:
        logger.info("Immediate notification for topic %r skipped: no SMTP credentials", topic)
        return

    if mailer.send(recipients, subject, body):
        state.set_immediate_last_sent(topic, now)
