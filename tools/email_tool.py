"""
AIDE / AIDE — Sprint 7: Email Tool
File: tools/email.py

Supports:
- Read emails (IMAP — Gmail, Outlook, any provider)
- Send emails (SMTP)
- Search inbox
- Reply to email
- Draft + queue (offline-safe)

Privacy covenant:
- Credentials stored locally in .env only
- No email content sent to cloud LLMs unless user explicitly approves
- All actions require safety gate confirmation (tier: notify or confirm)

Add to main.py:
    from tools.email import ReadEmailTool, SendEmailTool, SearchEmailTool, ReplyEmailTool

    tools = [
        ...
        ReadEmailTool(),
        SendEmailTool(),
        SearchEmailTool(),
        ReplyEmailTool(),
    ]

Required .env variables:
    EMAIL_ADDRESS=you@gmail.com
    EMAIL_PASSWORD=your_app_password       # Gmail: use App Password, not account password
    IMAP_HOST=imap.gmail.com               # or imap.outlook.com etc
    IMAP_PORT=993
    SMTP_HOST=smtp.gmail.com               # or smtp.outlook.com etc
    SMTP_PORT=587
    EMAIL_SAFETY_TIER=confirm              # 'notify' | 'confirm' | 'autonomous'

Gmail setup note:
    Enable IMAP in Gmail settings → Forwarding and POP/IMAP
    Generate App Password at: https://myaccount.google.com/apppasswords
    Use App Password as EMAIL_PASSWORD (not your Google account password)
"""

import imaplib
import smtplib
import email
import json
import re
from html import unescape
from email.utils import parsedate_to_datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import decode_header
from typing import Optional
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger
from tools.base import BaseTool

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_dotenv_values(path: str | None = None) -> dict[str, str]:
    if path is None:
        from core.settings import resolve_env_file
        env_path = resolve_env_file()
    else:
        env_path = Path(path)
    values: dict[str, str] = {}
    if not env_path.exists():
        return values

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _normalize_account_name(name: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", (name or "").strip().lower()).strip("_")
    return cleaned or "default"


def _account_aliases(*values: str) -> list[str]:
    aliases: list[str] = []
    seen = set()
    for value in values:
        if not value:
            continue
        for candidate in {value.strip().lower(), _normalize_account_name(value)}:
            if candidate and candidate not in seen:
                seen.add(candidate)
                aliases.append(candidate)
    return aliases


def _normalize_account_password(password: str, smtp_host: str) -> str:
    raw = (password or "").strip()
    if "gmail.com" not in (smtp_host or "").lower():
        return raw
    if re.fullmatch(r"[A-Za-z0-9]{4}( [A-Za-z0-9]{4}){3}", raw):
        return raw.replace(" ", "")
    return raw


def _build_email_accounts() -> dict[str, dict]:
    from core.settings import settings
    dotenv_values = _load_dotenv_values()
    accounts: dict[str, dict] = {}

    def add_account(name: str, config: dict, default: bool = False) -> None:
        if not config.get("address") or not config.get("password"):
            return
        account_name = _normalize_account_name(name)
        config = {
            **config,
            "password": _normalize_account_password(
                config.get("password", ""),
                config.get("smtp_host", ""),
            ),
        }
        aliases = _account_aliases(
            account_name,
            config.get("address", ""),
            *(config.get("aliases", []) or []),
        )
        accounts[account_name] = {
            **config,
            "name": account_name,
            "aliases": aliases,
            "default": default,
        }

    primary_aliases = dotenv_values.get("EMAIL_ALIASES", "")
    add_account(
        dotenv_values.get("DEFAULT_EMAIL_ACCOUNT", "default"),
        {
            "address": settings.email_address,
            "password": settings.email_password,
            "imap_host": settings.imap_host,
            "imap_port": settings.imap_port,
            "smtp_host": settings.smtp_host,
            "smtp_port": settings.smtp_port,
            "safety_tier": settings.email_safety_tier,
            "aliases": [a.strip() for a in primary_aliases.split(",") if a.strip()],
        },
        default=True,
    )

    add_account(
        "privateemail",
        {
            "address": settings.privateemail_address,
            "password": settings.privateemail_password,
            "imap_host": settings.privateemail_imap_server,
            "imap_port": 993,
            "smtp_host": settings.privateemail_smtp_server,
            "smtp_port": 587,
            "safety_tier": settings.email_safety_tier,
            "aliases": ["privateemail", "private", "work"],
        },
    )

    grouped: dict[str, dict[str, str]] = {}
    pattern = re.compile(r"^EMAIL_ACCOUNT_([A-Z0-9_]+)_(ADDRESS|PASSWORD|IMAP_HOST|IMAP_PORT|SMTP_HOST|SMTP_PORT|ALIASES)$")
    for key, value in dotenv_values.items():
        match = pattern.match(key)
        if not match:
            continue
        account_name, field = match.groups()
        grouped.setdefault(account_name.lower(), {})[field.lower()] = value

    for account_name, group in grouped.items():
        add_account(
            account_name,
            {
                "address": group.get("address", ""),
                "password": group.get("password", ""),
                "imap_host": group.get("imap_host", "imap.gmail.com"),
                "imap_port": int(group.get("imap_port", 993)),
                "smtp_host": group.get("smtp_host", "smtp.gmail.com"),
                "smtp_port": int(group.get("smtp_port", 587)),
                "safety_tier": settings.email_safety_tier,
                "aliases": [a.strip() for a in group.get("aliases", "").split(",") if a.strip()],
            },
            default=account_name == _normalize_account_name(dotenv_values.get("DEFAULT_EMAIL_ACCOUNT", "")),
        )

    return accounts


def _select_email_account(params: dict | None = None) -> dict:
    params = params or {}
    accounts = _build_email_accounts()
    if not accounts:
        return {}

    desired = (
        str(params.get("account") or params.get("from") or params.get("sender") or "")
        .strip()
        .lower()
    )

    if desired:
        normalized = _normalize_account_name(desired)
        for account in accounts.values():
            if desired == account["address"].lower():
                return account
            if normalized == account["name"] or normalized in account["aliases"]:
                return account

    for account in accounts.values():
        if account.get("default"):
            return account

    return next(iter(accounts.values()))


def _list_email_accounts() -> list[dict]:
    unique_accounts: list[dict] = []
    seen = set()
    for account in _build_email_accounts().values():
        key = (
            account.get("address", "").lower(),
            account.get("imap_host", "").lower(),
            int(account.get("imap_port", 993)),
        )
        if key in seen:
            continue
        seen.add(key)
        unique_accounts.append(account)
    return unique_accounts


def _is_google_email_account(account: dict) -> bool:
    host = (account.get("imap_host") or account.get("smtp_host") or "").lower()
    address = (account.get("address") or "").lower()
    return "gmail.com" in host or address.endswith("@gmail.com")


def _briefing_email_accounts() -> list[dict]:
    accounts = _list_email_accounts()
    google_accounts = [account for account in accounts if _is_google_email_account(account)]
    non_google_accounts = [account for account in accounts if not _is_google_email_account(account)]

    selected: list[dict] = []
    if google_accounts:
        primary_google = next((account for account in google_accounts if account.get("default")), google_accounts[0])
        selected.append(primary_google)
    selected.extend(non_google_accounts)
    return selected


def _decode_header_value(value: str) -> str:
    """Decode RFC2047-encoded email headers to plain string."""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _get_body(msg) -> str:
    """Extract readable plain-text body from email message."""
    html_fallback = None
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in disposition:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return _clean_email_text(payload.decode(charset, errors="replace"))
            if content_type == "text/html" and "attachment" not in disposition and html_fallback is None:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                html_fallback = payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return _html_to_text(text)
            return _clean_email_text(text)
    if html_fallback:
        return _html_to_text(html_fallback)
    return ""


def _clean_email_text(text: str) -> str:
    cleaned = unescape(text or "")
    cleaned = cleaned.replace("\r", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


def _html_to_text(html_text: str) -> str:
    text = html_text or ""
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?i)</div\s*>", "\n", text)
    text = re.sub(r"(?i)</li\s*>", "\n", text)
    text = re.sub(r"(?i)<li\s*>", "- ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\u00a0", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _connect_imap(cfg: dict) -> imaplib.IMAP4_SSL:
    """Open an authenticated IMAP connection."""
    mail = imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"])
    mail.login(cfg["address"], cfg["password"])
    return mail


def _account_help_text() -> str:
    accounts = _build_email_accounts()
    if not accounts:
        return "❌ Email not configured. Add EMAIL_ADDRESS/EMAIL_PASSWORD or EMAIL_ACCOUNT_<NAME>_* entries to .env"
    names = ", ".join(sorted(account["name"] for account in accounts.values()))
    return f"Configured accounts: {names}. Use an optional 'account' or 'from' field to choose one."


def _parse_email_summary(raw_msg_bytes: bytes, uid: str) -> dict:
    """Parse raw email bytes into a summary dict."""
    msg = email.message_from_bytes(raw_msg_bytes)
    return {
        "uid":      uid,
        "from":     _decode_header_value(msg.get("From", "")),
        "to":       _decode_header_value(msg.get("To", "")),
        "subject":  _decode_header_value(msg.get("Subject", "(no subject)")),
        "date":     msg.get("Date", ""),
        "snippet":  _get_body(msg)[:300].strip(),
        "body":     _get_body(msg),
        "reply_to": msg.get("Reply-To") or msg.get("From", ""),
        "message_id": msg.get("Message-ID", ""),
        "in_reply_to": msg.get("In-Reply-To", ""),
        "references": msg.get("References", ""),
    }


def _normalize_thread_subject(subject: str) -> str:
    normalized = (subject or "").strip().lower()
    normalized = re.sub(r"^(re|fwd|fw):\s*", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def fetch_email_message_by_uid(
    uid: str,
    *,
    account: str = "",
    folder: str = "INBOX",
) -> dict | None:
    params = {"account": account} if account else {}
    cfg = _select_email_account(params)
    if not cfg.get("address") or not cfg.get("password"):
        return None

    mail = _connect_imap(cfg)
    try:
        mail.select(folder)
        _, msg_data = mail.uid("fetch", uid.encode(), "(RFC822)")
        if not msg_data or not msg_data[0]:
            return None
        summary = _parse_email_summary(msg_data[0][1], uid)
        summary["account"] = cfg["name"]
        summary["account_address"] = cfg["address"]
        return summary
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def fetch_email_thread(
    uid: str,
    *,
    account: str = "",
    folder: str = "INBOX",
    scan_limit: int = 30,
) -> dict:
    original = fetch_email_message_by_uid(uid, account=account, folder=folder)
    if not original:
        return {"original": None, "messages": []}

    params = {"account": account} if account else {}
    cfg = _select_email_account(params)
    if not cfg.get("address") or not cfg.get("password"):
        return {"original": original, "messages": [original]}

    original_subject = _normalize_thread_subject(original.get("subject", ""))
    original_message_id = original.get("message_id", "")
    thread_messages: list[dict] = []

    mail = _connect_imap(cfg)
    try:
        mail.select(folder)
        _, data = mail.uid("search", None, "ALL")
        uids = data[0].split() if data and data[0] else []
        for related_uid in reversed(uids[-scan_limit:]):
            _, msg_data = mail.uid("fetch", related_uid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            summary = _parse_email_summary(msg_data[0][1], related_uid.decode())
            summary["account"] = cfg["name"]
            summary["account_address"] = cfg["address"]
            subject_match = _normalize_thread_subject(summary.get("subject", "")) == original_subject
            references = summary.get("references", "") or ""
            in_reply_to = summary.get("in_reply_to", "") or ""
            linked = (
                original_message_id
                and (original_message_id in references or original_message_id == in_reply_to)
            )
            same_message = summary.get("uid") == original.get("uid")
            if same_message or linked or subject_match:
                thread_messages.append(summary)
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    thread_messages.sort(key=lambda item: _parse_email_datetime(item.get("date", "")) or datetime.min.replace(tzinfo=timezone.utc))
    deduped: list[dict] = []
    seen_uids = set()
    for message in thread_messages:
        message_uid = message.get("uid")
        if message_uid in seen_uids:
            continue
        seen_uids.add(message_uid)
        deduped.append(message)

    return {
        "original": original,
        "messages": deduped or [original],
    }


def _parse_email_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fetch_recent_unread_briefing_emails(
    hours: int = 48,
    per_account_limit: int = 10,
    folder: str = "INBOX",
    now: datetime | None = None,
) -> list[dict]:
    """
    Return structured unread emails from all configured accounts that are recent
    enough to appear in the daily brief.
    """
    accounts = _briefing_email_accounts()
    if not accounts:
        return []

    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    cutoff = current_time - timedelta(hours=hours)
    results: list[dict] = []

    for account in accounts:
        try:
            mail = _connect_imap(account)
            try:
                mail.select(folder)
                _, data = mail.uid("search", None, "(UNSEEN)")
                uids = data[0].split() if data and data[0] else []

                for uid in reversed(uids[-per_account_limit:]):
                    _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    if not msg_data or not msg_data[0]:
                        continue
                    raw = msg_data[0][1]
                    summary = _parse_email_summary(raw, uid.decode())
                    received_at = _parse_email_datetime(summary.get("date", ""))
                    if not received_at or received_at < cutoff:
                        continue

                    results.append(
                        {
                            "uid": summary["uid"],
                            "from": summary["from"],
                            "subject": summary["subject"],
                            "snippet": summary["snippet"],
                            "body": summary["body"],
                            "received_at": received_at.isoformat(),
                            "account": account["name"],
                            "account_address": account["address"],
                            "reply_to": summary["reply_to"],
                            "message_id": summary["message_id"],
                        }
                    )
            finally:
                try:
                    mail.logout()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"Daily brief email fetch skipped for {account.get('address')}: {e}")

    results.sort(key=lambda item: item["received_at"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Tool 1 — Read recent emails
# ---------------------------------------------------------------------------

class ReadEmailTool(BaseTool):
    """
    Read recent emails from inbox.

    Input JSON:
        {
            "folder": "INBOX",          # optional, default INBOX
            "count":  10,               # how many to fetch, default 10
            "unread_only": false,       # optional, default false
            "account": "gmail"          # optional account alias/name/address
        }

    Output: list of email summaries (from, subject, date, snippet)
    """

    name = "read_email"
    description = (
        "Read recent emails from inbox. "
        "Returns sender, subject, date, and a short preview of each email. "
        "Use when the user asks to check email, see new messages, or read inbox."
    )

    async def execute(self, input_str: str) -> str:
        try:
            params = json.loads(input_str) if input_str.strip().startswith("{") else {}
        except json.JSONDecodeError:
            params = {}

        folder      = params.get("folder", "INBOX")
        count       = int(params.get("count", 10))
        unread_only = params.get("unread_only", False)

        cfg = _select_email_account(params)
        if not cfg["address"] or not cfg["password"]:
            return _account_help_text()

        try:
            mail = _connect_imap(cfg)
            mail.select(folder)

            search_criterion = "(UNSEEN)" if unread_only else "ALL"
            _, data = mail.uid("search", None, search_criterion)
            uids = data[0].split()

            if not uids:
                mail.logout()
                return "📭 No emails found."

            # Fetch most recent `count` emails
            recent_uids = uids[-count:]
            summaries = []

            for uid in reversed(recent_uids):
                _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                if msg_data and msg_data[0]:
                    raw = msg_data[0][1]
                    summary = _parse_email_summary(raw, uid.decode())
                    summaries.append(summary)

            mail.logout()

            lines = []
            for i, s in enumerate(summaries, 1):
                lines.append(
                    f"{i}. [{s['date'][:16]}] From: {s['from']}\n"
                    f"   Subject: {s['subject']}\n"
                    f"   Preview: {s['snippet'][:150]}...\n"
                    f"   UID: {s['uid']}"
                )
            return "\n\n".join(lines)

        except imaplib.IMAP4.error as e:
            logger.error(f"IMAP error: {e}")
            return f"❌ IMAP connection failed for {cfg['address']}: {e}"
        except Exception as e:
            logger.error(f"ReadEmailTool error: {e}")
            return f"❌ Error reading email: {e}"


# ---------------------------------------------------------------------------
# Tool 2 — Send email
# ---------------------------------------------------------------------------

class SendEmailTool(BaseTool):
    """
    Send an email via SMTP.

    Input JSON:
        {
            "to":      "recipient@example.com",
            "subject": "Hello",
            "body":    "Message body here",
            "account": "gmail"
        }

    Requires safety confirmation before sending.
    """

    name = "send_email"
    description = (
        "Send an email to someone. "
        "Requires: recipient address, subject, and message body. "
        "Optional: specify which configured email account to send from using account or from. "
        "Use when the user asks to send, write, or compose an email."
    )
    requires_confirmation = True   # Safety gate flag — agent.py checks this

    async def execute(self, input_str: str) -> str:
        try:
            params = json.loads(input_str)
        except json.JSONDecodeError:
            return "❌ Invalid input. Expected JSON with 'to', 'subject', 'body'."

        # Normalize field name aliases that LLM might generate
        to = self._normalize_field(params, ["to", "recipient", "recipient_address", "email", "email_address", "destination"])
        subject = self._normalize_field(params, ["subject", "title"])
        body = self._normalize_field(params, ["body", "message", "message_body", "content", "text", "text_body"])
        
        # Normalize account field
        account_field = self._normalize_field(params, ["account", "from", "from_email", "sender", "from_account"])
        if account_field:
            params["account"] = account_field

        to = to.strip() if to else ""
        subject = subject.strip() if subject else ""
        body = body.strip() if body else ""

        if not to or not subject or not body:
            return "❌ Missing fields. Need: to, subject, body."

        cfg = _select_email_account(params)
        if not cfg["address"] or not cfg["password"]:
            return _account_help_text()

        try:
            msg = MIMEMultipart()
            msg["From"]    = cfg["address"]
            msg["To"]      = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
                server.ehlo()
                server.starttls()
                server.login(cfg["address"], cfg["password"])
                server.sendmail(cfg["address"], to, msg.as_string())

            logger.info(f"Email sent from {cfg['address']} to {to} | Subject: {subject}")
            return f"✅ Email sent to {to}\nFrom: {cfg['address']}\nSubject: {subject}"

        except smtplib.SMTPAuthenticationError:
            return f"❌ SMTP authentication failed for {cfg['address']}. Check the configured password or app password for that account."
        except smtplib.SMTPException as e:
            logger.error(f"SMTP error: {e}")
            return f"❌ Failed to send email from {cfg['address']}: {e}"
        except Exception as e:
            logger.error(f"SendEmailTool error: {e}")
            return f"❌ Error sending email from {cfg['address']}: {e}"

    def _normalize_field(self, params: dict, field_aliases: list[str]) -> str:
        """Extract value from params using a list of possible field name aliases."""
        for alias in field_aliases:
            value = params.get(alias, "")
            if value and str(value).strip():
                return str(value).strip()
        return ""


# ---------------------------------------------------------------------------
# Tool 3 — Search inbox
# ---------------------------------------------------------------------------

class SearchEmailTool(BaseTool):
    """
    Search inbox by keyword, sender, or subject.

    Input JSON:
        {
            "query":  "invoice",           # keyword to search
            "field":  "subject",           # "subject" | "from" | "body" | "all"
            "count":  5,                   # max results, default 5
            "account": "gmail"             # optional account alias/name/address
        }
    """

    name = "search_email"
    description = (
        "Search emails in inbox by keyword, sender, or subject. "
        "Use when the user asks to find a specific email, look for messages from someone, "
        "or search for an email about a topic."
    )

    async def execute(self, input_str: str) -> str:
        try:
            params = json.loads(input_str) if input_str.strip().startswith("{") else {}
        except json.JSONDecodeError:
            params = {"query": input_str.strip()}

        query = params.get("query", "").strip()
        field = params.get("field", "all").lower()
        count = int(params.get("count", 5))

        if not query:
            return "❌ No search query provided."

        cfg = _select_email_account(params)
        if not cfg["address"] or not cfg["password"]:
            return _account_help_text()

        # Build IMAP search criterion
        imap_field_map = {
            "subject": f'SUBJECT "{query}"',
            "from":    f'FROM "{query}"',
            "body":    f'BODY "{query}"',
            "all":     f'TEXT "{query}"',
        }
        criterion = imap_field_map.get(field, f'TEXT "{query}"')

        try:
            mail = _connect_imap(cfg)
            mail.select("INBOX")

            _, data = mail.uid("search", None, criterion)
            uids = data[0].split()

            if not uids:
                mail.logout()
            return f"📭 No emails found matching '{query}' in {cfg['address']}."

            recent_uids = uids[-count:]
            results = []

            for uid in reversed(recent_uids):
                _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                if msg_data and msg_data[0]:
                    raw = msg_data[0][1]
                    summary = _parse_email_summary(raw, uid.decode())
                    results.append(summary)

            mail.logout()

            lines = []
            for i, s in enumerate(results, 1):
                lines.append(
                    f"{i}. [{s['date'][:16]}] From: {s['from']}\n"
                    f"   Subject: {s['subject']}\n"
                    f"   Preview: {s['snippet'][:200]}\n"
                    f"   UID: {s['uid']}"
                )
            return f"🔍 Found {len(results)} email(s) for '{query}' in {cfg['address']}:\n\n" + "\n\n".join(lines)

        except Exception as e:
            logger.error(f"SearchEmailTool error: {e}")
            return f"❌ Search failed: {e}"


# ---------------------------------------------------------------------------
# Tool 4 — Reply to email
# ---------------------------------------------------------------------------

class ReplyEmailTool(BaseTool):
    """
    Reply to a specific email by UID.

    Input JSON:
        {
            "uid":   "12345",              # UID from read_email or search_email
            "body":  "Reply text here",
            "account": "gmail"
        }

    Requires safety confirmation before sending.
    """

    name = "reply_email"
    description = (
        "Reply to a specific email. "
        "Requires the email UID (from read_email or search_email) and the reply body. "
        "Use when the user asks to reply to, respond to, or answer an email."
    )
    requires_confirmation = True

    async def execute(self, input_str: str) -> str:
        try:
            params = json.loads(input_str)
        except json.JSONDecodeError:
            return "❌ Invalid input. Expected JSON with 'uid' and 'body'."

        uid  = str(params.get("uid", "")).strip()
        body = params.get("body", "").strip()

        if not uid or not body:
            return "❌ Missing fields. Need: uid and body."

        cfg = _select_email_account(params)
        if not cfg["address"] or not cfg["password"]:
            return _account_help_text()

        try:
            # Fetch original email to get reply headers
            mail = _connect_imap(cfg)
            mail.select("INBOX")

            _, msg_data = mail.uid("fetch", uid.encode(), "(RFC822)")
            if not msg_data or not msg_data[0]:
                mail.logout()
                return f"❌ Email with UID {uid} not found."

            original = _parse_email_summary(msg_data[0][1], uid)
            mail.logout()

            # Build reply
            reply = MIMEMultipart()
            reply["From"]       = cfg["address"]
            reply["To"]         = original["reply_to"]
            reply["Subject"]    = f"Re: {original['subject']}"
            reply["In-Reply-To"] = original["message_id"]
            reply["References"]  = original["message_id"]

            full_body = (
                f"{body}\n\n"
                f"---\n"
                f"On {original['date']}, {original['from']} wrote:\n"
                f"{original['snippet'][:500]}"
            )
            reply.attach(MIMEText(full_body, "plain"))

            with smtplib.SMTP(cfg["smtp_host"], cfg["smtp_port"]) as server:
                server.ehlo()
                server.starttls()
                server.login(cfg["address"], cfg["password"])
                server.sendmail(cfg["address"], original["reply_to"], reply.as_string())

            logger.info(f"Reply sent from {cfg['address']} to {original['reply_to']} | Subject: Re: {original['subject']}")
            return f"✅ Reply sent to {original['reply_to']}\nFrom: {cfg['address']}\nSubject: Re: {original['subject']}"

        except smtplib.SMTPAuthenticationError:
            return f"❌ SMTP authentication failed for {cfg['address']}."
        except Exception as e:
            logger.error(f"ReplyEmailTool error: {e}")
            return f"❌ Reply failed from {cfg['address']}: {e}"
