"""Tests for src.notifications.mailer (WP-44): config loading, Mailer.send,
and notify_immediate. No test here ever opens a real socket - every send
goes through a fake transport shaped like smtplib.SMTP (constructor, context
manager, .starttls()/.login()/.send_message()).
"""

import smtplib
from datetime import datetime

import pytest
import yaml

from src.notifications.mailer import (
    ENV_SMTP_PASSWORD, ENV_SMTP_USERNAME, IMMEDIATE_RATE_LIMIT, Mailer, load_email_config,
    notify_immediate, smtp_configured,
)
from src.storage.notifications import NotificationStateStore, NotificationSubscriptionsStore


def _write_notifications_yaml(config_dir, **email_overrides):
    email = {
        "enabled": True,
        "smtp_host": "smtp.example.com",
        "smtp_port": 587,
        "smtp_use_tls": True,
        "smtp_username": "user@example.com",
        "smtp_password": "hunter2",
        "from_email": "policypulse@example.com",
    }
    email.update(email_overrides)
    (config_dir / "notifications.yaml").write_text(
        yaml.dump({"email": email}), encoding="utf-8",
    )


class _FakeSMTP:
    """Stands in for smtplib.SMTP - constructor, context manager, and the
    three methods Mailer.send calls. Records calls on `recorder` (a list
    shared across instances via a closure) and can be made to fail at a
    chosen step via `fail_at`.
    """

    def __init__(self, host, port, timeout=30):
        self.host = host
        self.port = port

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def starttls(self, context=None):
        self.recorder.append(("starttls",))
        if self.fail_at == "starttls":
            raise smtplib.SMTPException("starttls failed")

    def login(self, username, password):
        self.recorder.append(("login", username, password))
        if self.fail_at == "login":
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")

    def send_message(self, msg):
        self.recorder.append(("send_message", msg))
        if self.fail_at == "send_message":
            raise OSError("connection reset")


def _fake_transport(recorder, fail_at=None):
    def factory(host, port, timeout=30):
        instance = _FakeSMTP(host, port, timeout)
        instance.recorder = recorder
        instance.fail_at = fail_at
        return instance
    return factory


class _ExplodingTransport:
    """A transport whose constructor itself raises - the "can't even
    connect" case."""

    def __init__(self, host, port, timeout=30):
        raise OSError("connection refused")


# ---------------------------------------------------------------------------
# load_email_config / smtp_configured
# ---------------------------------------------------------------------------

class TestLoadEmailConfig:
    @pytest.mark.medium
    def test_missing_file_is_unconfigured(self, tmp_path):
        config = load_email_config(str(tmp_path))
        assert config["enabled"] is False
        assert config["smtp_host"] == ""

    @pytest.mark.medium
    def test_loads_yaml_values(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        config = load_email_config(str(tmp_path))
        assert config["enabled"] is True
        assert config["smtp_host"] == "smtp.example.com"
        assert config["smtp_port"] == 587
        assert config["smtp_username"] == "user@example.com"
        assert config["smtp_password"] == "hunter2"
        assert config["from_email"] == "policypulse@example.com"

    @pytest.mark.medium
    def test_env_overrides_blank_yaml_credentials(self, tmp_path, monkeypatch):
        _write_notifications_yaml(tmp_path, smtp_username="", smtp_password="")
        monkeypatch.setenv(ENV_SMTP_USERNAME, "env-user")
        monkeypatch.setenv(ENV_SMTP_PASSWORD, "env-pass")
        config = load_email_config(str(tmp_path))
        assert config["smtp_username"] == "env-user"
        assert config["smtp_password"] == "env-pass"

    @pytest.mark.medium
    def test_env_wins_over_yaml_credentials(self, tmp_path, monkeypatch):
        _write_notifications_yaml(tmp_path)
        monkeypatch.setenv(ENV_SMTP_USERNAME, "env-user")
        monkeypatch.setenv(ENV_SMTP_PASSWORD, "env-pass")
        config = load_email_config(str(tmp_path))
        assert config["smtp_username"] == "env-user"
        assert config["smtp_password"] == "env-pass"


class TestSmtpConfigured:
    @pytest.mark.small
    def test_true_when_everything_present(self):
        assert smtp_configured({
            "enabled": True, "smtp_host": "h", "smtp_username": "u",
            "smtp_password": "p", "from_email": "f@example.com",
        }) is True

    @pytest.mark.small
    def test_false_when_disabled(self):
        assert smtp_configured({
            "enabled": False, "smtp_host": "h", "smtp_username": "u",
            "smtp_password": "p", "from_email": "f@example.com",
        }) is False

    @pytest.mark.small
    @pytest.mark.parametrize("missing", ["smtp_host", "smtp_username", "smtp_password", "from_email"])
    def test_false_when_a_required_field_is_missing(self, missing):
        config = {
            "enabled": True, "smtp_host": "h", "smtp_username": "u",
            "smtp_password": "p", "from_email": "f@example.com",
        }
        config[missing] = ""
        assert smtp_configured(config) is False


# ---------------------------------------------------------------------------
# Mailer.send
# ---------------------------------------------------------------------------

class TestMailerSend:
    @pytest.mark.medium
    def test_unconfigured_never_touches_transport(self, tmp_path):
        recorder: list = []
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        assert mailer.send(["a@example.com"], "subj", "body") is False
        assert recorder == []

    @pytest.mark.medium
    def test_empty_recipients_returns_false(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        recorder: list = []
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        assert mailer.send([], "subj", "body") is False
        assert recorder == []

    @pytest.mark.medium
    def test_success_logs_in_and_sends(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        recorder: list = []
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        assert mailer.send(["a@example.com"], "Subject line", "Body text") is True
        assert ("login", "user@example.com", "hunter2") in recorder
        sent = [call for call in recorder if call[0] == "send_message"]
        assert len(sent) == 1
        msg = sent[0][1]
        assert msg["Subject"] == "Subject line"
        assert msg["From"] == "policypulse@example.com"
        assert msg["To"] == "a@example.com"

    @pytest.mark.medium
    def test_starttls_skipped_when_tls_disabled(self, tmp_path):
        _write_notifications_yaml(tmp_path, smtp_use_tls=False)
        recorder: list = []
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        mailer.send(["a@example.com"], "subj", "body")
        assert ("starttls",) not in recorder

    @pytest.mark.medium
    def test_login_failure_returns_false_and_records_error(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        recorder: list = []
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path),
            transport=_fake_transport(recorder, fail_at="login"),
        )
        assert mailer.send(["a@example.com"], "subj", "body") is False
        error = NotificationStateStore(data_dir=str(tmp_path)).get_last_send_error()
        assert error is not None
        assert "bad credentials" in error["error"] or "535" in error["error"]

    @pytest.mark.medium
    def test_send_failure_oserror_returns_false_and_records_error(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        recorder: list = []
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path),
            transport=_fake_transport(recorder, fail_at="send_message"),
        )
        assert mailer.send(["a@example.com"], "subj", "body") is False
        error = NotificationStateStore(data_dir=str(tmp_path)).get_last_send_error()
        assert error["error"] == "connection reset"

    @pytest.mark.medium
    def test_transport_connect_failure_returns_false(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        mailer = Mailer(
            config_dir=str(tmp_path), data_dir=str(tmp_path), transport=_ExplodingTransport,
        )
        assert mailer.send(["a@example.com"], "subj", "body") is False
        error = NotificationStateStore(data_dir=str(tmp_path)).get_last_send_error()
        assert error["error"] == "connection refused"


# ---------------------------------------------------------------------------
# notify_immediate
# ---------------------------------------------------------------------------

class TestNotifyImmediate:
    @pytest.mark.medium
    def test_no_subscribers_never_touches_transport(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        recorder: list = []
        notify_immediate(
            "ops_alerts", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        assert recorder == []

    @pytest.mark.medium
    def test_unconfigured_smtp_never_touches_transport(self, tmp_path):
        NotificationSubscriptionsStore(data_dir=str(tmp_path)).create(
            email="a@example.com", topics=["ops_alerts"], frequency="immediate",
        )
        recorder: list = []
        notify_immediate(
            "ops_alerts", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        assert recorder == []

    @pytest.mark.medium
    def test_sends_only_to_immediate_subscribers_of_the_topic(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        subs = NotificationSubscriptionsStore(data_dir=str(tmp_path))
        subs.create(email="immediate@example.com", topics=["ops_alerts"], frequency="immediate")
        subs.create(email="daily@example.com", topics=["ops_alerts"], frequency="daily")
        subs.create(email="wrong-topic@example.com", topics=["early_signals"], frequency="immediate")
        recorder: list = []
        notify_immediate(
            "ops_alerts", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path),
            transport=_fake_transport(recorder),
        )
        sent = [call for call in recorder if call[0] == "send_message"]
        assert len(sent) == 1
        assert sent[0][1]["To"] == "immediate@example.com"

    @pytest.mark.medium
    def test_two_calls_within_an_hour_send_once(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        NotificationSubscriptionsStore(data_dir=str(tmp_path)).create(
            email="a@example.com", topics=["ops_alerts"], frequency="immediate",
        )
        recorder: list = []
        transport = _fake_transport(recorder)
        t0 = datetime(2026, 1, 5, 6, 0, 0)

        notify_immediate(
            "ops_alerts", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path), transport=transport, now=t0,
        )
        notify_immediate(
            "ops_alerts", "subj 2", "body 2",
            data_dir=str(tmp_path), config_dir=str(tmp_path), transport=transport,
            now=t0 + (IMMEDIATE_RATE_LIMIT / 2),
        )

        sent = [call for call in recorder if call[0] == "send_message"]
        assert len(sent) == 1

    @pytest.mark.medium
    def test_rate_limit_clears_after_an_hour(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        NotificationSubscriptionsStore(data_dir=str(tmp_path)).create(
            email="a@example.com", topics=["ops_alerts"], frequency="immediate",
        )
        recorder: list = []
        transport = _fake_transport(recorder)
        t0 = datetime(2026, 1, 5, 6, 0, 0)

        notify_immediate(
            "ops_alerts", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path), transport=transport, now=t0,
        )
        notify_immediate(
            "ops_alerts", "subj 2", "body 2",
            data_dir=str(tmp_path), config_dir=str(tmp_path), transport=transport,
            now=t0 + IMMEDIATE_RATE_LIMIT + (IMMEDIATE_RATE_LIMIT / 10),
        )

        sent = [call for call in recorder if call[0] == "send_message"]
        assert len(sent) == 2

    @pytest.mark.medium
    def test_different_topics_are_independent(self, tmp_path):
        _write_notifications_yaml(tmp_path)
        subs = NotificationSubscriptionsStore(data_dir=str(tmp_path))
        subs.create(email="a@example.com", topics=["ops_alerts"], frequency="immediate")
        subs.create(email="b@example.com", topics=["early_signals"], frequency="immediate")
        recorder: list = []
        transport = _fake_transport(recorder)
        t0 = datetime(2026, 1, 5, 6, 0, 0)

        notify_immediate(
            "ops_alerts", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path), transport=transport, now=t0,
        )
        notify_immediate(
            "early_signals", "subj", "body",
            data_dir=str(tmp_path), config_dir=str(tmp_path), transport=transport, now=t0,
        )

        sent = [call for call in recorder if call[0] == "send_message"]
        assert len(sent) == 2
