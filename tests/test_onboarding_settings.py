from pathlib import Path


def test_settings_can_be_loaded_from_environment(monkeypatch):
    from core.settings import Settings

    monkeypatch.setenv("DEFAULT_LLM", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    monkeypatch.setenv("MEMORY_DB_PATH", "./data/test-memory.db")

    settings = Settings(_env_file=None)

    assert settings.default_llm == "openai"
    assert settings.openai_api_key == "test-openai-key"
    assert str(settings.memory_db_path) == "data/test-memory.db"


def test_mvp_config_persists_env_and_module_settings(tmp_path, monkeypatch):
    from core import mvp_config

    env_path = tmp_path / ".env"
    module_path = tmp_path / "modules.json"
    monkeypatch.setattr(mvp_config, "ENV_PATH", env_path)
    monkeypatch.setattr(mvp_config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(mvp_config, "MODULE_CONFIG_PATH", module_path)

    mvp_config.write_env_values(
        {
            "DEFAULT_LLM": "ollama",
            "OPENAI_API_KEY": "abc123",
        }
    )
    saved_modules = mvp_config.save_module_settings({"finance": True, "fitness": True})

    assert mvp_config.read_env_values()["DEFAULT_LLM"] == "ollama"
    assert 'OPENAI_API_KEY="abc123"' in env_path.read_text(encoding="utf-8")
    assert saved_modules["finance"] is True
    assert saved_modules["fitness"] is False


def test_onboarding_flow_persists_completion(tmp_path, monkeypatch, capsys):
    from onboarding.flow import OnboardingFlow, OnboardingStep

    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    flow = OnboardingFlow()
    flow.set_user_name(" Frank ")
    flow.set_operator_name(" AIDE ")

    assert flow.get_greeting() == "Nice to meet you, Frank."
    assert flow.get_intro_message() == "I'm AIDE. I'll help prepare your day privately on this device."

    flow.complete_onboarding()

    output = capsys.readouterr().out
    assert "Onboarding complete" in output
    assert flow.get_current_step() == OnboardingStep.COMPLETE
    assert (tmp_path / ".aide" / "onboarding.json").exists()
