"""
AIDE — settings
Loads configuration from .env and provides typed access throughout the app.

Runtime data paths default to ~/.aide/ rather than a relative ./data/
directory. This matters because PyInstaller app bundles are read-only
after macOS notarisation -- a relative path resolves against whatever
directory the bundle happens to be launched from, which is unpredictable
and frequently not writable. ~/.aide/ is always a real, writable location
regardless of how AIDE was started or packaged. This mirrors the existing
convention already used by mesh/identity.py, mesh/device_registry.py,
mesh/trust_graph.py, daily_brief/storage.py, and onboarding/storage.py.

Each user-data path below uses Field(default_factory=...) rather than a
literal Path(...) default. A literal default is evaluated once, at class
definition time, when the module is first imported -- which is normally
harmless, but it means the path can't depend on anything computed at
runtime. default_factory defers evaluation until a Settings() instance is
actually constructed, so _aide_data_dir() always reflects the real home
directory of the user running the bundle, not the machine that built it.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal
from pathlib import Path


def _aide_root_dir() -> Path:
    """~/.aide -- the canonical app data root, for files that don't live under data/."""
    return Path.home() / ".aide"


def _aide_data_dir() -> Path:
    """~/.aide/data -- the canonical writable data directory."""
    return _aide_root_dir() / "data"


def _resolve_env_file() -> str:
    """
    Where pydantic-settings should load .env from.

    This has to be computed independently of Settings.__init__ -- pydantic-
    settings reads model_config["env_file"] before any field default_factory
    runs, so this can't simply reuse _aide_data_dir() through a field; it
    needs its own standalone resolution at module import time.

    Precedence:
      1. ~/.aide/.env if it exists -- this is where the setup wizard
         (core/setup_wizard.py) writes credentials, and where a packaged
         app's data necessarily lives once the bundle itself is read-only.
      2. ./.env if it exists -- preserves the existing developer workflow
         for anyone running AIDE from a git checkout with a repo-local
         .env, so this change doesn't break every existing dev setup.
      3. ~/.aide/.env even if neither exists yet -- so a brand new install
         with no .env at all still ends up pointed at the location the
         wizard will write to, rather than a relative path that resolves
         differently depending on the current working directory.
    """
    aide_env = _aide_root_dir() / ".env"
    if aide_env.exists():
        return str(aide_env)
    repo_env = Path(".env")
    if repo_env.exists():
        return str(repo_env)
    return str(aide_env)


def resolve_env_file() -> Path:
    """
    Public, Path-returning version of _resolve_env_file(). Other modules
    that read .env directly for their own purposes (daily_brief/calendar.py,
    tools/email_tool.py) import this rather than hardcoding their own
    ".env" default, so there is exactly one place that decides where
    .env actually lives.
    """
    return Path(_resolve_env_file())


class Settings(BaseSettings):
    # ── Telegram ────────────────────────────────────────────────
    telegram_bot_token: str = Field("", description="From @BotFather")
    telegram_stream_replies: bool = False

    # ── Email (Gmail primary) ────────────────────────────────────
    email_address: str = ""
    email_password: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    email_safety_tier: str = "approve"

    # ── Email (PrivateEmail / Namecheap) ─────────────────────────
    privateemail_address: str = ""
    privateemail_password: str = ""
    privateemail_imap_server: str = "mail.privateemail.com"
    privateemail_smtp_server: str = "mail.privateemail.com"

    # ── LLM ─────────────────────────────────────────────────────
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_local_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:e4b"
    ollama_fallback_model: str = "qwen2.5-coder:3b"
    ollama_chat_max_tokens: int = 1024
    ollama_reasoning_max_tokens: int = 2048
    ollama_keep_alive: str = "30m"
    groq_api_key: str = ""
    default_llm: str = "groq"
    groq_model: str = "llama-3.1-70b-versatile"
    tavily_api_key: str = ""
    gemini_api_key: str = ""
    deepseek_api_key: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ── Web search ──────────────────────────────────────────────
    searxng_url: str = "http://localhost:8080"

    # ── Memory ──────────────────────────────────────────────────
    memory_db_path: Path = Field(default_factory=lambda: _aide_data_dir() / "aide_memory.db")
    chroma_db_path: Path = Field(default_factory=lambda: _aide_data_dir() / "chroma")
    daily_brief_dir: Path = Field(default_factory=lambda: _aide_root_dir() / "daily_briefs")
    obsidian_vault_path: Path = Path("~/Obsidian/AIDEMemory")
    obsidian_sync_interval_seconds: int = 300
    google_calendar_credentials_path: Path = Field(
        default_factory=lambda: _aide_data_dir() / "google" / "calendar_credentials.json"
    )
    google_calendar_token_path: Path = Field(
        default_factory=lambda: _aide_data_dir() / "google" / "calendar_token.json"
    )
    google_calendar_id: str = "primary"
    google_calendar_label: str = "Google Calendar"
    google_calendar_timezone: str = "UTC"
    calendar_ics_path: str = ""
    calendar_ics_label: str = "Local Calendar"
    calendar_ics_timezone: str = "UTC"

    # ── Mesh ────────────────────────────────────────────────────
    mesh_port: int = 7432
    mesh_service_name: str = "_aide._tcp.local."

    # ── Agent behaviour ─────────────────────────────────────────
    context_window_size: int = 20
    default_safety_tier: Literal["autonomous", "notify", "approve"] = "notify"
    approval_timeout_seconds: int = 1800

    # ── ChromaDB telemetry (silence warnings) ────────────────────
    chroma_telemetry: str = "False"
    anonymized_telemetry: str = "False"

    # ── Logging ─────────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: Path = Field(default_factory=lambda: _aide_data_dir() / "aide.log")

    # ── Web interface ───────────────────────────────────────────
    web_host: str = "0.0.0.0"
    web_port: int = 3000

    model_config = {
        "env_file": _resolve_env_file(),
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Single shared instance
settings = Settings()  # type: ignore[call-arg]

# Ensure data directories exist. Each path is created independently --
# they no longer share a common parent by accident, so each needs its
# own mkdir call.
settings.memory_db_path.parent.mkdir(parents=True, exist_ok=True)
settings.chroma_db_path.mkdir(parents=True, exist_ok=True)
settings.daily_brief_dir.mkdir(parents=True, exist_ok=True)
settings.google_calendar_credentials_path.parent.mkdir(parents=True, exist_ok=True)
settings.google_calendar_token_path.parent.mkdir(parents=True, exist_ok=True)
settings.log_file.parent.mkdir(parents=True, exist_ok=True)
