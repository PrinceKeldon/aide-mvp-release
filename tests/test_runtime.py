import pytest


def test_main_instance_lock_uses_configured_lock_path(tmp_path, monkeypatch):
    import main

    lock_path = tmp_path / "aide.lock"
    monkeypatch.setattr(main, "INSTANCE_LOCK_PATH", lock_path)

    main.acquire_instance_lock()

    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8").strip()

    main.release_instance_lock()

    assert not lock_path.exists()


@pytest.mark.asyncio
async def test_llm_available_providers_reflect_connectivity_and_keys(monkeypatch):
    from core.llm import LLMClient
    from core.settings import settings

    client = LLMClient()

    async def ollama_running():
        return True

    monkeypatch.setattr(client, "is_ollama_running", ollama_running)
    monkeypatch.setattr(settings, "groq_api_key", "groq-key")
    monkeypatch.setattr(settings, "gemini_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "openai-key")

    assert await client.available_providers() == ["ollama", "groq", "openai"]


@pytest.mark.asyncio
async def test_llm_available_providers_handles_offline_ollama(monkeypatch):
    from core.llm import LLMClient
    from core.settings import settings

    client = LLMClient()

    async def ollama_down():
        return False

    monkeypatch.setattr(client, "is_ollama_running", ollama_down)
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "gemini-key")
    monkeypatch.setattr(settings, "openai_api_key", "")

    assert await client.available_providers() == ["gemini"]
