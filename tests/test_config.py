from studio import config


def clean_env(monkeypatch):
    for key in list(config.os.environ):
        if key.startswith(("STUDIO_", "LLM_")):
            monkeypatch.delenv(key)


def test_user_llm_aliases_and_secret_repr(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text(
        "LLM_CHAT_COMPLETIONS_URL=https://example.test/v1/chat/completions/\n"
        "LLM_MODEL=test-model\nLLM_API_KEY=test-secret\n"
    )
    settings = config.Settings.from_env()
    assert settings.base_url == "https://example.test/v1"
    assert settings.model_id == "test-model"
    assert settings.api_key == "test-secret"
    assert "test-secret" not in repr(settings)
    assert settings.mode == "extractive"  # Credentials alone do not bypass policy.


def test_explicit_studio_settings_take_precedence(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("LLM_CHAT_COMPLETIONS_URL", "https://alias.test/v1/chat/completions")
    monkeypatch.setenv("LLM_MODEL", "alias")
    monkeypatch.setenv("LLM_API_KEY", "alias-key")
    monkeypatch.setenv("STUDIO_MODEL_BASE_URL", "https://explicit.test/v1/")
    monkeypatch.setenv("STUDIO_MODEL_ID", "explicit")
    monkeypatch.setenv("STUDIO_MODEL_API_KEY", "explicit-key")
    settings = config.Settings.from_env()
    assert settings.base_url == "https://explicit.test/v1"
    assert settings.model_id == "explicit"
    assert settings.api_key == "explicit-key"


def test_local_env_does_not_overwrite_environment(tmp_path, monkeypatch):
    clean_env(monkeypatch)
    monkeypatch.setattr(config, "ROOT", tmp_path)
    (tmp_path / ".env").write_text("LLM_MODEL=from-file\nPATH=untrusted\n")
    monkeypatch.setenv("LLM_MODEL", "from-process")
    path = config.os.environ.get("PATH")
    assert config.Settings.from_env().model_id == "from-process"
    assert config.os.environ.get("PATH") == path
