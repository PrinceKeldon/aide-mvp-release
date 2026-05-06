"""UI-facing configuration for the Phase 1 MVP."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


CONFIG_DIR = Path.home() / ".aide"
MODULE_CONFIG_PATH = CONFIG_DIR / "modules.json"
ONBOARDING_PATH = CONFIG_DIR / "onboarding.json"
ENV_PATH = Path(".env")


@dataclass(frozen=True)
class ModuleDefinition:
    key: str
    name: str
    description: str
    data_access: str
    internet: str
    default_enabled: bool
    phase: str = "Phase 1"
    coming_soon: bool = False


MODULES: tuple[ModuleDefinition, ...] = (
    ModuleDefinition(
        "vera_core",
        "AIDE Core",
        "Chat, memory, model routing, task planning, and safety approvals.",
        "Conversation memory and local system state.",
        "Optional cloud model access if configured.",
        True,
    ),
    ModuleDefinition(
        "daily_brief",
        "Your Day",
        "Daily brief, proactive reminders, and morning summaries.",
        "Calendar summaries, reminders, project notes, and recent context.",
        "Optional, depending on enabled sources.",
        True,
    ),
    ModuleDefinition(
        "telegram",
        "Telegram Mobile",
        "Use Telegram as the mobile interface with no app-store install.",
        "Telegram chat messages and approval responses.",
        "Required.",
        True,
    ),
    ModuleDefinition(
        "email",
        "Email",
        "Read, search, draft, and send email with approval controls.",
        "Configured mailboxes and selected message content.",
        "Required.",
        False,
    ),
    ModuleDefinition(
        "calendar",
        "Calendar",
        "Read events, detect meetings, and prepare schedule-aware brief items.",
        "Configured calendar events and meeting metadata.",
        "Required for Google Calendar.",
        False,
    ),
    ModuleDefinition(
        "browser",
        "Browser Tools",
        "Fetch pages, monitor prices, and help with web tasks.",
        "Pages AIDE is asked to open or monitor.",
        "Required.",
        False,
    ),
    ModuleDefinition(
        "projects",
        "Projects",
        "Track long-running projects, next actions, contacts, and notes.",
        "Project notes and task history.",
        "Optional.",
        False,
    ),
    ModuleDefinition(
        "finance",
        "FinanceOS",
        "Ingest bank PDFs, categorize transactions, and produce monthly reports.",
        "Local transaction data and budget targets.",
        "No for transaction analysis; local Ollama only.",
        False,
    ),
    ModuleDefinition(
        "fitness",
        "Fit Genie",
        "Coming soon. Health and fitness coaching is excluded from the MVP shipping build.",
        "No data access while disabled.",
        "None.",
        False,
        phase="Coming soon",
        coming_soon=True,
    ),
    ModuleDefinition(
        "peer_mesh",
        "Owner Mesh",
        "Pair trusted devices and route tasks between them.",
        "Trusted device registry and mesh task metadata.",
        "Local network.",
        False,
        phase="Phase 2",
    ),
)


def module_defaults() -> dict[str, bool]:
    return {module.key: module.default_enabled for module in MODULES}


def load_module_settings() -> dict[str, bool]:
    settings = module_defaults()
    try:
        data = json.loads(MODULE_CONFIG_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for key, enabled in data.items():
                if key in settings:
                    settings[key] = bool(enabled)
    except FileNotFoundError:
        pass
    except Exception:
        pass
    settings["fitness"] = False
    return settings


def save_module_settings(values: dict[str, Any]) -> dict[str, bool]:
    settings = load_module_settings()
    for key, enabled in values.items():
        if key in settings and key not in {"vera_core", "fitness"}:
            settings[key] = bool(enabled)
    settings["vera_core"] = True
    settings["fitness"] = False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    MODULE_CONFIG_PATH.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def module_payload() -> list[dict[str, Any]]:
    enabled = load_module_settings()
    return [
        {
            **asdict(module),
            "enabled": enabled.get(module.key, module.default_enabled),
            "locked": module.key == "vera_core" or module.coming_soon,
        }
        for module in MODULES
    ]


def read_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    for raw in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env_values(updates: dict[str, str]) -> None:
    existing_lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []
    for raw in existing_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            next_lines.append(raw)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={_quote_env(updates[key])}")
            seen.add(key)
        else:
            next_lines.append(raw)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={_quote_env(value)}")
    ENV_PATH.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")
    for key, value in updates.items():
        os.environ[key] = value


def _quote_env(value: str) -> str:
    if value == "":
        return ""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def provider_status() -> dict[str, Any]:
    env = read_env_values()
    email_address = env.get("EMAIL_ADDRESS") or os.environ.get("EMAIL_ADDRESS") or ""
    email_password = env.get("EMAIL_PASSWORD") or os.environ.get("EMAIL_PASSWORD") or ""
    google_credentials_path = (
        env.get("GOOGLE_CALENDAR_CREDENTIALS_PATH")
        or os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_PATH")
        or "./data/google/calendar_credentials.json"
    )
    google_token_path = (
        env.get("GOOGLE_CALENDAR_TOKEN_PATH")
        or os.environ.get("GOOGLE_CALENDAR_TOKEN_PATH")
        or "./data/google/calendar_token.json"
    )
    google_calendar_id = (
        env.get("GOOGLE_CALENDAR_ID")
        or env.get("CALENDAR_ACCOUNT_GOOGLE_PRIMARY_GOOGLE_CALENDAR_ID")
        or os.environ.get("GOOGLE_CALENDAR_ID")
        or "primary"
    )
    ics_path = (
        env.get("CALENDAR_ICS_PATH")
        or env.get("CALENDAR_ACCOUNT_LOCAL_ICS_PATH")
        or os.environ.get("CALENDAR_ICS_PATH")
        or ""
    )
    return {
        "agent_name": agent_name(),
        "default_llm": env.get("DEFAULT_LLM") or os.environ.get("DEFAULT_LLM") or "groq",
        "providers": {
            "groq": bool(env.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")),
            "gemini": bool(env.get("GEMINI_API_KEY") or os.environ.get("GEMINI_API_KEY")),
            "openai": bool(env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")),
            "ollama": True,
        },
        "telegram_configured": bool(
            env.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
        ),
        "integrations": {
            "email_configured": bool(email_address and email_password),
            "google_calendar_configured": bool(
                google_calendar_id
                and Path(google_credentials_path).expanduser().exists()
                and Path(google_token_path).expanduser().exists()
            ),
            "local_calendar_configured": bool(ics_path),
        },
        "integration_values": {
            "email_address": email_address,
            "imap_host": env.get("IMAP_HOST") or os.environ.get("IMAP_HOST") or "imap.gmail.com",
            "imap_port": env.get("IMAP_PORT") or os.environ.get("IMAP_PORT") or "993",
            "smtp_host": env.get("SMTP_HOST") or os.environ.get("SMTP_HOST") or "smtp.gmail.com",
            "smtp_port": env.get("SMTP_PORT") or os.environ.get("SMTP_PORT") or "587",
            "email_safety_tier": env.get("EMAIL_SAFETY_TIER") or os.environ.get("EMAIL_SAFETY_TIER") or "approve",
            "google_calendar_credentials_path": google_credentials_path,
            "google_calendar_token_path": google_token_path,
            "google_calendar_id": google_calendar_id,
            "google_calendar_label": env.get("GOOGLE_CALENDAR_LABEL")
            or env.get("CALENDAR_ACCOUNT_GOOGLE_PRIMARY_LABEL")
            or os.environ.get("GOOGLE_CALENDAR_LABEL")
            or "Google Calendar",
            "google_calendar_timezone": env.get("GOOGLE_CALENDAR_TIMEZONE")
            or env.get("CALENDAR_ACCOUNT_GOOGLE_PRIMARY_TIMEZONE")
            or os.environ.get("GOOGLE_CALENDAR_TIMEZONE")
            or "UTC",
            "calendar_ics_path": ics_path,
            "calendar_ics_label": env.get("CALENDAR_ICS_LABEL")
            or env.get("CALENDAR_ACCOUNT_LOCAL_LABEL")
            or os.environ.get("CALENDAR_ICS_LABEL")
            or "Local Calendar",
            "calendar_ics_timezone": env.get("CALENDAR_ICS_TIMEZONE")
            or env.get("CALENDAR_ACCOUNT_LOCAL_TIMEZONE")
            or os.environ.get("CALENDAR_ICS_TIMEZONE")
            or "UTC",
        },
    }


def agent_name() -> str:
    try:
        data = json.loads(ONBOARDING_PATH.read_text(encoding="utf-8"))
        name = str(data.get("operator_name") or "AIDE").strip() or "AIDE"
        legacy_name = "".join(["v", "e", "r", "a"])
        return "AIDE" if name.lower() == legacy_name else name
    except Exception:
        return "AIDE"


def onboarding_complete() -> bool:
    try:
        data = json.loads(ONBOARDING_PATH.read_text(encoding="utf-8"))
        return bool(data.get("onboarding_completed"))
    except Exception:
        return False
