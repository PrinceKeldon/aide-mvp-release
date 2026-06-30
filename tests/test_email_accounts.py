import smtplib

import pytest

from core.settings import settings
from tools.email_tool import SendEmailTool, _select_email_account


class FakeSMTP:
    sent = []

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.username = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def ehlo(self):
        return None

    def starttls(self):
        return None

    def login(self, username, password):
        self.username = username
        self.password = password

    def sendmail(self, from_addr, to_addr, msg):
        FakeSMTP.sent.append(
            {
                "host": self.host,
                "port": self.port,
                "from_addr": from_addr,
                "to_addr": to_addr,
                "msg": msg,
                "username": self.username,
            }
        )


@pytest.fixture
def isolated_email_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DEFAULT_EMAIL_ACCOUNT=gmail",
                "EMAIL_ALIASES=gmail,personal",
                "EMAIL_ACCOUNT_GMAIL_ADDRESS=frankkoine@gmail.com",
                "EMAIL_ACCOUNT_GMAIL_PASSWORD=gmailapppassword",
                "EMAIL_ACCOUNT_GMAIL_IMAP_HOST=imap.gmail.com",
                "EMAIL_ACCOUNT_GMAIL_IMAP_PORT=993",
                "EMAIL_ACCOUNT_GMAIL_SMTP_HOST=smtp.gmail.com",
                "EMAIL_ACCOUNT_GMAIL_SMTP_PORT=587",
                "EMAIL_ACCOUNT_GMAIL_ALIASES=gmail,personal",
                "EMAIL_ACCOUNT_WORK_ADDRESS=exec@frankkoine.com",
                "EMAIL_ACCOUNT_WORK_PASSWORD=privateemailpass",
                "EMAIL_ACCOUNT_WORK_IMAP_HOST=mail.privateemail.com",
                "EMAIL_ACCOUNT_WORK_IMAP_PORT=993",
                "EMAIL_ACCOUNT_WORK_SMTP_HOST=mail.privateemail.com",
                "EMAIL_ACCOUNT_WORK_SMTP_PORT=587",
                "EMAIL_ACCOUNT_WORK_ALIASES=work,privateemail",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(settings, "email_address", "")
    monkeypatch.setattr(settings, "email_password", "")
    monkeypatch.setattr(settings, "imap_host", "imap.gmail.com")
    monkeypatch.setattr(settings, "imap_port", 993)
    monkeypatch.setattr(settings, "smtp_host", "smtp.gmail.com")
    monkeypatch.setattr(settings, "smtp_port", 587)
    monkeypatch.setattr(settings, "privateemail_address", "")
    monkeypatch.setattr(settings, "privateemail_password", "")
    monkeypatch.setattr(settings, "privateemail_imap_server", "mail.privateemail.com")
    monkeypatch.setattr(settings, "privateemail_smtp_server", "mail.privateemail.com")


def test_select_email_account_uses_named_alias(isolated_email_env):
    gmail = _select_email_account({"account": "gmail"})
    work = _select_email_account({"account": "privateemail"})
    default = _select_email_account({})

    assert gmail["address"] == "frankkoine@gmail.com"
    assert work["address"] == "exec@frankkoine.com"
    assert default["address"] == "frankkoine@gmail.com"


@pytest.mark.asyncio
async def test_send_email_uses_selected_account(isolated_email_env, monkeypatch):
    FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    tool = SendEmailTool()

    result = await tool.execute(
        '{"to":"friend@example.com","subject":"Hello","body":"Testing","account":"work"}'
    )

    assert "From: exec@frankkoine.com" in result
    assert FakeSMTP.sent[0]["host"] == "mail.privateemail.com"
    assert FakeSMTP.sent[0]["username"] == "exec@frankkoine.com"
