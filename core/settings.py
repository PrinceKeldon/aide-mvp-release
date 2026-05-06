"""
AIDE — settings
Loads configuration from .env and provides typed access throughout the app.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Literal
from pathlib import Path


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
    ollama_chat_max_tokens: int = 256
    ollama_reasoning_max_tokens: int = 768
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
    memory_db_path: Path = Path("./data/aide_memory.db")
    chroma_db_path: Path = Path("./data/chroma")
    daily_brief_dir: Path = Path("./data/daily_briefs")
    obsidian_vault_path: Path = Path("~/Obsidian/AIDEMemory")
    obsidian_sync_interval_seconds: int = 300
    google_calendar_credentials_path: Path = Path(
        "./data/google/calendar_credentials.json"
    )
    google_calendar_token_path: Path = Path("./data/google/calendar_token.json")
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
    log_file: Path = Path("./data/aide.log")

    # ── Web interface ───────────────────────────────────────────
    web_host: str = "0.0.0.0"
    web_port: int = 3000

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


# Single shared instance
settings = Settings()  # type: ignore[call-arg]

# Ensure data directories exist
settings.memory_db_path.parent.mkdir(parents=True, exist_ok=True)
settings.chroma_db_path.mkdir(parents=True, exist_ok=True)
settings.daily_brief_dir.mkdir(parents=True, exist_ok=True)
