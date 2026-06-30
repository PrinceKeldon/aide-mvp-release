from datetime import datetime, timedelta, timezone, date
from email.mime.text import MIMEText

import pytest

from core.settings import settings
from daily_brief.context import gather_daily_context
from daily_brief.schema import DailyBrief, DailyBriefSummary
from daily_brief.storage import load_daily_brief, save_daily_brief
from tools.email_tool import fetch_recent_unread_briefing_emails


def _sample_brief() -> DailyBrief:
    return DailyBrief(
        date="2026-04-01",
        summary=DailyBriefSummary(
            headline="Good morning. I've prepared your day.",
            counts={"draft_messages": 0, "schedule_suggestions": 0, "prepared_tasks": 0, "approval_requests": 0},
        ),
        items=[],
        generated_at=datetime.now(),
    )


def _build_email(from_addr: str, subject: str, body: str, sent_at: datetime) -> bytes:
    msg = MIMEText(body, "plain")
    msg["From"] = from_addr
    msg["To"] = "owner@example.com"
    msg["Subject"] = subject
    msg["Date"] = sent_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Message-ID"] = f"<{subject.replace(' ', '_')}@example.com>"
    return msg.as_bytes()


def _build_html_email(from_addr: str, subject: str, html_body: str, sent_at: datetime) -> bytes:
    msg = MIMEText(html_body, "html")
    msg["From"] = from_addr
    msg["To"] = "owner@example.com"
    msg["Subject"] = subject
    msg["Date"] = sent_at.strftime("%a, %d %b %Y %H:%M:%S +0000")
    msg["Message-ID"] = f"<{subject.replace(' ', '_')}@example.com>"
    return msg.as_bytes()


class FakeIMAP:
    messages_by_host = {}

    def __init__(self, host, port):
        self.host = host
        self.port = port
        self.messages = self.messages_by_host.get(host, [])

    def login(self, username, password):
        self.username = username
        self.password = password

    def select(self, folder):
        self.folder = folder
        return "OK", [b""]

    def uid(self, command, *args):
        if command == "search":
            uids = b" ".join(str(item["uid"]).encode() for item in self.messages)
            return "OK", [uids]
        if command == "fetch":
            uid = args[0]
            uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
            for item in self.messages:
                if str(item["uid"]) == uid_str:
                    return "OK", [(b"RFC822", item["raw"])]
            return "OK", []
        raise AssertionError(f"Unsupported IMAP command: {command}")

    def logout(self):
        return "BYE", [b""]


@pytest.fixture
def isolated_phase2_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DEFAULT_EMAIL_ACCOUNT=gmail",
                "EMAIL_ACCOUNT_GMAIL_ADDRESS=frankkoine@gmail.com",
                "EMAIL_ACCOUNT_GMAIL_PASSWORD=gmailapppassword",
                "EMAIL_ACCOUNT_GMAIL_IMAP_HOST=imap.gmail.com",
                "EMAIL_ACCOUNT_GMAIL_IMAP_PORT=993",
                "EMAIL_ACCOUNT_GMAIL_SMTP_HOST=smtp.gmail.com",
                "EMAIL_ACCOUNT_GMAIL_SMTP_PORT=587",
                "EMAIL_ACCOUNT_GMAIL_ALIASES=gmail,personal",
                "EMAIL_ACCOUNT_KELDON_ADDRESS=keldontechnologies24@gmail.com",
                "EMAIL_ACCOUNT_KELDON_PASSWORD=keldonapppassword",
                "EMAIL_ACCOUNT_KELDON_IMAP_HOST=imap.gmail.com",
                "EMAIL_ACCOUNT_KELDON_IMAP_PORT=993",
                "EMAIL_ACCOUNT_KELDON_SMTP_HOST=smtp.gmail.com",
                "EMAIL_ACCOUNT_KELDON_SMTP_PORT=587",
                "EMAIL_ACCOUNT_KELDON_ALIASES=keldon,business,secondary",
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
    monkeypatch.setattr(settings, "daily_brief_dir", tmp_path / "data" / "daily_briefs")


def test_daily_brief_storage_uses_settings_data_path(isolated_phase2_env):
    brief = _sample_brief()

    save_daily_brief(brief)
    loaded = load_daily_brief(date.fromisoformat(brief.date))

    assert (settings.daily_brief_dir / f"{brief.date}.json").exists()
    assert loaded["date"] == brief.date
    assert loaded["summary"]["headline"] == brief.summary.headline


def test_load_daily_brief_sanitizes_stale_html_email_items(isolated_phase2_env):
    payload = {
        "date": "2026-04-02",
        "summary": {
            "headline": "Good morning. I've prepared your day.",
            "counts": {"email_digest": 1, "unread_emails": 1},
        },
        "items": [
            {
                "id": "email_1",
                "type": "email_digest",
                "priority": "medium",
                "title": "Email: Join us",
                "reason": "Unread in the last 48 hours.",
                "content": {
                    "subject": "Join us",
                    "summary": "<!DOCTYPE html><html><body><h1>Activity Invitation</h1><p>Join us at 11am.</p></body></html>",
                    "body": "<!DOCTYPE html><html><body><h1>Activity Invitation</h1><p>Join us at 11am.</p></body></html>",
                },
                "actions": ["draft_reply", "dismiss"],
                "created_at": datetime.now().isoformat(),
            }
        ],
        "generated_at": datetime.now().isoformat(),
    }
    settings.daily_brief_dir.mkdir(parents=True, exist_ok=True)
    (settings.daily_brief_dir / "2026-04-02.json").write_text(__import__("json").dumps(payload), encoding="utf-8")

    loaded = load_daily_brief(date(2026, 4, 2))

    email_item = loaded["items"][0]
    assert "<html" not in email_item["content"]["summary"].lower()
    assert "<!doctype" not in email_item["content"]["body"].lower()
    assert "Activity Invitation" in email_item["content"]["body"]


def test_load_daily_brief_limits_email_summary_to_four_sentences(isolated_phase2_env):
    payload = {
        "date": "2026-04-02",
        "summary": {"headline": "Good morning.", "counts": {"email_digest": 1, "unread_emails": 1}},
        "items": [
            {
                "id": "email_2",
                "type": "email_digest",
                "priority": "medium",
                "title": "Email: Long update",
                "reason": "Unread in the last 48 hours.",
                "content": {
                    "subject": "Long update",
                    "summary": "One. Two. Three. Four. Five. Six.",
                    "body": "One. Two. Three. Four. Five. Six.",
                },
                "actions": [],
                "created_at": datetime.now().isoformat(),
            }
        ],
        "generated_at": datetime.now().isoformat(),
    }
    settings.daily_brief_dir.mkdir(parents=True, exist_ok=True)
    (settings.daily_brief_dir / "2026-04-02.json").write_text(__import__("json").dumps(payload), encoding="utf-8")

    loaded = load_daily_brief(date(2026, 4, 2))

    summary = loaded["items"][0]["content"]["summary"]
    assert summary == "One. Two. Three. Four."


def test_fetch_recent_unread_email_digest_filters_to_last_48_hours(isolated_phase2_env, monkeypatch):
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)
    FakeIMAP.messages_by_host = {
        "imap.gmail.com": [
            {"uid": "1", "raw": _build_email("Recent Gmail <sender@gmail.com>", "Recent Gmail", "Fresh body", now - timedelta(hours=3))},
            {"uid": "2", "raw": _build_email("Old Gmail <sender@gmail.com>", "Old Gmail", "Old body", now - timedelta(hours=72))},
            {"uid": "3", "raw": _build_email("Secondary Gmail <sender@gmail.com>", "Secondary Gmail", "Secondary body", now - timedelta(hours=2))},
        ],
        "mail.privateemail.com": [
            {"uid": "11", "raw": _build_email("Exec Sender <client@company.com>", "Recent Work", "Work body", now - timedelta(hours=12))},
            {"uid": "12", "raw": _build_email("Older Work <client@company.com>", "Old Work", "Old work body", now - timedelta(hours=60))},
        ],
    }

    monkeypatch.setattr("tools.email_tool._connect_imap", lambda cfg: FakeIMAP(cfg["imap_host"], cfg["imap_port"]))

    emails = fetch_recent_unread_briefing_emails(hours=48, now=now)

    assert len(emails) == 3
    assert {email["subject"] for email in emails} == {"Recent Gmail", "Secondary Gmail", "Recent Work"}
    assert {email["account"] for email in emails} == {"gmail", "work"}
    assert all(email["received_at"] >= (now - timedelta(hours=48)).isoformat() for email in emails)


def test_fetch_recent_unread_email_digest_uses_only_primary_google_inbox(isolated_phase2_env, monkeypatch):
    now = datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc)

    class RoutedFakeIMAP(FakeIMAP):
        def login(self, username, password):
            self.username = username
            self.password = password
            if username == "frankkoine@gmail.com":
                self.messages = [
                    {"uid": "1", "raw": _build_email("Primary Gmail <sender@gmail.com>", "Primary Gmail", "Fresh body", now - timedelta(hours=3))},
                ]
            elif username == "keldontechnologies24@gmail.com":
                self.messages = [
                    {"uid": "2", "raw": _build_email("Secondary Gmail <sender@gmail.com>", "Secondary Gmail", "Secondary body", now - timedelta(hours=2))},
                ]
            elif username == "exec@frankkoine.com":
                self.messages = [
                    {"uid": "11", "raw": _build_email("Exec Sender <client@company.com>", "Recent Work", "Work body", now - timedelta(hours=12))},
                ]
            else:
                self.messages = []

    def _connect(cfg):
        mail = RoutedFakeIMAP(cfg["imap_host"], cfg["imap_port"])
        mail.login(cfg["address"], cfg["password"])
        return mail

    monkeypatch.setattr("tools.email_tool._connect_imap", _connect)

    emails = fetch_recent_unread_briefing_emails(hours=48, now=now)

    assert {email["subject"] for email in emails} == {"Primary Gmail", "Recent Work"}
    assert "Secondary Gmail" not in {email["subject"] for email in emails}


def test_gather_daily_context_includes_unread_email_digest(isolated_phase2_env, monkeypatch):
    fake_emails = [
        {
            "uid": "1",
            "from": "Sender <sender@example.com>",
            "subject": "Test subject",
            "snippet": "Snippet",
            "body": "Body",
            "received_at": "2026-04-01T11:00:00+00:00",
            "account": "gmail",
            "account_address": "frankkoine@gmail.com",
            "reply_to": "sender@example.com",
            "message_id": "<msg@example.com>",
        }
    ]

    monkeypatch.setattr("daily_brief.context._gather_from_memory", lambda target_date: [])
    monkeypatch.setattr("daily_brief.context._gather_recent_context", lambda: [])
    monkeypatch.setattr("daily_brief.context.fetch_recent_unread_briefing_emails", lambda hours=48: fake_emails)

    context = gather_daily_context(date(2026, 4, 1))

    assert context["unread_emails"] == fake_emails


def test_fetch_recent_unread_email_digest_strips_html_markup(isolated_phase2_env, monkeypatch):
    now = datetime(2026, 4, 2, 9, 0, tzinfo=timezone.utc)

    class HtmlFakeIMAP(FakeIMAP):
        def login(self, username, password):
            self.username = username
            self.password = password
            self.messages = [
                {
                    "uid": "1",
                    "raw": _build_html_email(
                        "Events <events@example.com>",
                        "Join us: Digital Nomadism / Live Conference",
                        """
                        <!DOCTYPE html>
                        <html>
                          <head><style>.hidden{display:none}</style></head>
                          <body>
                            <h1>Activity Invitation</h1>
                            <p>Join us for the live conference.</p>
                            <div>Date: Friday at 11am</div>
                          </body>
                        </html>
                        """,
                        now - timedelta(hours=2),
                    ),
                }
            ]

    def _connect(cfg):
        mail = HtmlFakeIMAP(cfg["imap_host"], cfg["imap_port"])
        mail.login(cfg["address"], cfg["password"])
        return mail

    monkeypatch.setattr("tools.email_tool._connect_imap", _connect)

    emails = fetch_recent_unread_briefing_emails(hours=48, now=now)

    assert len(emails) == 2  # primary gmail + work, but only gmail has data here? no, helper adds non-google too
    html_email = next(email for email in emails if email["subject"] == "Join us: Digital Nomadism / Live Conference")
    assert "<html" not in html_email["snippet"].lower()
    assert "<!doctype" not in html_email["body"].lower()
    assert "Activity Invitation" in html_email["body"]
    assert "Join us for the live conference." in html_email["snippet"]
