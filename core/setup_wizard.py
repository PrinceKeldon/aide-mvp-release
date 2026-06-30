"""
AIDE -- Setup Wizard backend
Reads and writes ~/.aide/.env without disturbing keys the wizard doesn't
touch. Used by the /setup web routes in interface/web.py.

This is the CREDENTIALS wizard -- not to be confused with onboarding/flow.py,
which handles display-name personalisation (what to call the user, what to
call AIDE) after credentials are already configured. The two are sequential
in the UI (setup wizard Screen 4 hands off into the name flow) but are
deliberately separate modules: one writes secrets to .env, the other writes
preferences to onboarding.json. Mixing them would make the secrets path
harder to audit.

Design constraints:
- Never deletes a key that exists in .env, even if the wizard doesn't know
  about it -- a hand-edited .env with extra keys must survive a save.
- Preserves line ordering; only touches lines whose key the wizard owns.
- If ~/.aide/.env doesn't exist yet, starts from the repo's .env.example
  as a template, so the file gets sensible defaults for every other field.
- Never writes secrets to logs.
- Never reads or returns raw secret values to the frontend -- only a
  masked preview, so a screenshotted /api/setup/status response can't
  leak a credential.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.settings import settings, resolve_env_file

# Where the wizard WRITES is always ~/.aide/.env -- deliberately not the
# same as resolve_env_file()'s read-time precedence, which falls back to
# a repo-local ./.env for developer convenience. A packaged end-user has
# no repo to fall back to, and a developer testing the wizard against a
# checkout shouldn't have saves land in version-controlled territory by
# accident. Writing is unconditionally ~/.aide/.env; reading (everywhere
# else in the app, via resolve_env_file()) prefers it but tolerates a
# repo-local override if that's what's actually on disk.
ENV_PATH = settings.memory_db_path.parent.parent / ".env"
ENV_EXAMPLE_PATH = Path(__file__).resolve().parent.parent / ".env.example"

# The only keys the wizard is allowed to write. Anything else in .env,
# however it got there, is left untouched on every save.
WIZARD_MANAGED_KEYS = {
    "TELEGRAM_BOT_TOKEN",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "DEFAULT_EMAIL_ACCOUNT",
    "EMAIL_ADDRESS",
    "EMAIL_PASSWORD",
    "EMAIL_ALIASES",
    "IMAP_HOST",
    "IMAP_PORT",
    "SMTP_HOST",
    "SMTP_PORT",
    "MORNING_BRIEF_HOUR",
    "GOOGLE_CALENDAR_CREDENTIALS_PATH",
    "GOOGLE_CALENDAR_TOKEN_PATH",
    "GOOGLE_CALENDAR_ID",
    "GOOGLE_CALENDAR_LABEL",
    "GOOGLE_CALENDAR_TIMEZONE",
    "CALENDAR_ICS_PATH",
    "CALENDAR_ICS_LABEL",
    "CALENDAR_ICS_TIMEZONE",
}

_EMAIL_ACCOUNT_PATTERN = re.compile(
    r"^EMAIL_ACCOUNT_[A-Z0-9_]+_(ADDRESS|PASSWORD|IMAP_HOST|IMAP_PORT|SMTP_HOST|SMTP_PORT|ALIASES)$"
)
_CALENDAR_ACCOUNT_PATTERN = re.compile(
    r"^CALENDAR_ACCOUNT_[A-Z0-9_]+_(ICS_PATH|LABEL|ALIASES|TIMEZONE|OWNER_IDENTITY|PROVIDER|GOOGLE_CALENDAR_ID)$"
)


def _read_env_lines() -> list[str]:
    if ENV_PATH.exists():
        return ENV_PATH.read_text(encoding="utf-8").splitlines()
    if ENV_EXAMPLE_PATH.exists():
        return ENV_EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    return []


def _read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    key_pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
    for line in _read_env_lines():
        match = key_pattern.match(line)
        if match:
            values[match.group(1)] = match.group(2).strip().strip('"').strip("'")
    return values


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return value[:4] + "•" * (len(value) - 8) + value[-4:]


async def check_ollama_reachable() -> bool:
    """
    Real check, not a trust-the-user checkbox. Reuses the same probe
    /status already uses, so the wizard and the status page never
    disagree about whether Ollama is actually running.
    """
    from core.llm import LLMClient
    try:
        return await LLMClient().is_ollama_running()
    except Exception:
        return False


def get_setup_status() -> dict:
    """
    Report what's already configured, without ever returning raw
    secret values. Used by the wizard to skip steps already done, and
    by the chat page to decide whether to redirect to /setup at all.
    """
    values = _read_env_values()
    telegram_token = values.get("TELEGRAM_BOT_TOKEN") or settings.telegram_bot_token
    groq_key = values.get("GROQ_API_KEY") or settings.groq_api_key
    gemini_key = values.get("GEMINI_API_KEY") or settings.gemini_api_key
    openai_key = values.get("OPENAI_API_KEY") or settings.openai_api_key
    has_telegram = bool(telegram_token.strip())
    has_groq = bool(groq_key.strip())
    has_gemini = bool(gemini_key.strip())
    has_openai = bool(openai_key.strip())
    has_cloud_llm = has_groq or has_gemini or has_openai

    return {
        "telegram_configured": has_telegram,
        "telegram_masked": _mask(telegram_token) if has_telegram else "",
        "groq_configured": has_groq,
        "groq_masked": _mask(groq_key) if has_groq else "",
        "gemini_configured": has_gemini,
        "openai_configured": has_openai,
        "has_any_cloud_llm": has_cloud_llm,
        "email_accounts": _email_account_status(),
        "calendar_sources": _calendar_source_status(),
        "your_day_ready": bool(_email_account_status()) and bool(_calendar_source_status()),
        "morning_brief_hour": _read_env_values().get("MORNING_BRIEF_HOUR", "7"),
        # Minimum bar to leave /setup: at least one LLM path is reachable.
        # Ollama is checked live by the frontend via /api/setup/ollama-check
        # rather than folded in here, because it requires an await and
        # this function is called from a sync context in one place.
        "setup_complete": has_cloud_llm,
    }


def _email_account_status() -> list[dict[str, str]]:
    values = _read_env_values()
    accounts: list[dict[str, str]] = []

    if values.get("EMAIL_ADDRESS") and values.get("EMAIL_PASSWORD"):
        accounts.append(
            {
                "name": values.get("DEFAULT_EMAIL_ACCOUNT", "default"),
                "address_masked": _mask(values.get("EMAIL_ADDRESS", "")),
                "imap_host": values.get("IMAP_HOST", "imap.gmail.com"),
                "smtp_host": values.get("SMTP_HOST", "smtp.gmail.com"),
            }
        )

    grouped: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        match = re.match(
            r"^EMAIL_ACCOUNT_([A-Z0-9_]+)_(ADDRESS|PASSWORD|IMAP_HOST|IMAP_PORT|SMTP_HOST|SMTP_PORT|ALIASES)$",
            key,
        )
        if not match:
            continue
        name, field = match.groups()
        grouped.setdefault(name.lower(), {})[field.lower()] = value

    for name, config in grouped.items():
        if config.get("address") and config.get("password"):
            accounts.append(
                {
                    "name": name,
                    "address_masked": _mask(config.get("address", "")),
                    "imap_host": config.get("imap_host", "imap.gmail.com"),
                    "smtp_host": config.get("smtp_host", "smtp.gmail.com"),
                }
            )

    return accounts


def _calendar_source_status() -> list[dict[str, str]]:
    values = _read_env_values()
    sources: list[dict[str, str]] = []

    if values.get("CALENDAR_ICS_PATH"):
        sources.append(
            {
                "name": "local",
                "provider": "ics",
                "label": values.get("CALENDAR_ICS_LABEL", "Local Calendar"),
                "detail": values.get("CALENDAR_ICS_PATH", ""),
            }
        )

    if values.get("GOOGLE_CALENDAR_ID"):
        token_path = Path(values.get("GOOGLE_CALENDAR_TOKEN_PATH") or settings.google_calendar_token_path).expanduser()
        sources.append(
            {
                "name": "google_primary",
                "provider": "google",
                "label": values.get("GOOGLE_CALENDAR_LABEL", "Google Calendar"),
                "detail": values.get("GOOGLE_CALENDAR_ID", "primary"),
                "authorized": "true" if token_path.exists() else "false",
            }
        )

    grouped: dict[str, dict[str, str]] = {}
    for key, value in values.items():
        match = re.match(
            r"^CALENDAR_ACCOUNT_([A-Z0-9_]+)_(ICS_PATH|LABEL|ALIASES|TIMEZONE|OWNER_IDENTITY|PROVIDER|GOOGLE_CALENDAR_ID)$",
            key,
        )
        if not match:
            continue
        name, field = match.groups()
        grouped.setdefault(name.lower(), {})[field.lower()] = value

    for name, config in grouped.items():
        provider = (config.get("provider") or ("google" if config.get("google_calendar_id") else "ics")).lower()
        if provider == "ics" and not config.get("ics_path"):
            continue
        if provider == "google" and not config.get("google_calendar_id"):
            continue
        sources.append(
            {
                "name": name,
                "provider": provider,
                "label": config.get("label", name.replace("_", " ").title()),
                "detail": config.get("google_calendar_id") or config.get("ics_path", ""),
            }
        )

    return sources


def write_setup_values(values: dict[str, str]) -> dict:
    """
    Patch ~/.aide/.env with only the keys present in `values` AND present
    in WIZARD_MANAGED_KEYS. Every other line is preserved exactly as-is.

    Empty-string values are treated as "leave unchanged" -- submitting a
    blank field for a step the user skipped must never blank out a
    working key that was set in a previous session.
    """
    to_write = {
        k: v for k, v in values.items()
        if (k in WIZARD_MANAGED_KEYS or _EMAIL_ACCOUNT_PATTERN.match(k) or _CALENDAR_ACCOUNT_PATTERN.match(k))
        and v
        and v.strip()
    }
    if not to_write:
        return {"written": [], "skipped_unknown_or_empty": list(values.keys())}

    lines = _read_env_lines()
    seen: set[str] = set()
    new_lines: list[str] = []
    key_pattern = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")

    for line in lines:
        match = key_pattern.match(line)
        if not match:
            new_lines.append(line)
            continue
        key = match.group(1)
        if key in to_write:
            new_lines.append(f"{key}={to_write[key]}")
            seen.add(key)
        else:
            new_lines.append(line)

    for key, value in to_write.items():
        if key not in seen:
            new_lines.append(f"{key}={value}")

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    try:
        ENV_PATH.chmod(0o600)
    except Exception:
        pass

    return {
        "written": list(to_write.keys()),
        "skipped_unknown_or_empty": [k for k in values if k not in to_write],
    }


def normalize_setup_name(value: str, fallback: str = "default") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (value or "").strip()).strip("_")
    return (cleaned or fallback).upper()


def save_display_names(user_display_name: str | None, operator_name: str | None) -> None:
    """
    Hands off into the existing name-personalisation flow (Screen 4 of
    the wizard). Deliberately thin -- onboarding/flow.py already owns
    the state transitions and persistence model, so the wizard just
    delegates to it rather than re-implementing onboarding writes.
    """
    from onboarding.flow import OnboardingFlow

    flow = OnboardingFlow()
    flow.set_user_name(user_display_name)
    flow.set_operator_name(operator_name)
    flow.complete_onboarding()
