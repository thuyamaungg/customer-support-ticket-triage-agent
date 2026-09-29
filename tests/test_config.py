import pytest

from app.config import DEFAULT_TRIAGE_MODELS, Settings
from app.triage import model_settings_for


def test_gemini_is_the_default(monkeypatch):
    monkeypatch.delenv("TRIAGE_PROVIDER", raising=False)
    monkeypatch.delenv("TRIAGE_MODEL", raising=False)
    s = Settings()
    assert s.triage_provider == "gemini"
    assert s.triage_model.startswith("google:")


@pytest.mark.parametrize("provider", ["gemini", "openai", "claude", " Claude "])
def test_each_provider_gets_its_default_model(monkeypatch, provider):
    monkeypatch.setenv("TRIAGE_PROVIDER", provider)
    monkeypatch.delenv("TRIAGE_MODEL", raising=False)
    s = Settings()
    assert s.triage_model == DEFAULT_TRIAGE_MODELS[provider.strip().lower()]


def test_explicit_model_overrides_default(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "gemini")
    monkeypatch.setenv("TRIAGE_MODEL", "google:gemini-2.5-flash")
    assert Settings().triage_model == "google:gemini-2.5-flash"


def test_unknown_provider_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "mistral")
    with pytest.raises(ValueError, match="TRIAGE_PROVIDER"):
        Settings()


def test_temperature_only_where_supported():
    assert model_settings_for("google:gemini-flash-latest")["temperature"] == 0
    assert model_settings_for("anthropic:claude-haiku-4-5")["temperature"] == 0
    assert "temperature" not in model_settings_for("openai:gpt-5.4-mini")


# ---- fallback models ------------------------------------------------------

def test_provider_default_fallbacks(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "gemini")
    monkeypatch.delenv("TRIAGE_MODEL", raising=False)
    monkeypatch.delenv("TRIAGE_FALLBACK_MODELS", raising=False)
    s = Settings()
    assert s.triage_model_chain[0] == s.triage_model
    assert len(s.triage_model_chain) > 1


def test_main_model_is_never_its_own_backup(monkeypatch):
    monkeypatch.setenv("TRIAGE_PROVIDER", "gemini")
    monkeypatch.setenv("TRIAGE_MODEL", "google:gemini-3.6-flash")
    monkeypatch.delenv("TRIAGE_FALLBACK_MODELS", raising=False)
    s = Settings()
    assert s.triage_model_chain.count("google:gemini-3.6-flash") == 1   # it's also a default backup
    assert "google:gemini-3.5-flash-lite" in s.triage_fallback_models


def test_custom_and_empty_fallbacks(monkeypatch):
    monkeypatch.setenv("TRIAGE_FALLBACK_MODELS", " openai:gpt-4.1-mini , anthropic:claude-haiku-4-5 ,")
    assert Settings().triage_fallback_models == ("openai:gpt-4.1-mini", "anthropic:claude-haiku-4-5")
    monkeypatch.setenv("TRIAGE_FALLBACK_MODELS", "")
    assert Settings().triage_fallback_models == ()


def test_no_temperature_if_any_model_in_chain_is_openai():
    assert "temperature" not in model_settings_for(("google:gemini-2.5-flash", "openai:gpt-5.4-mini"))
    assert model_settings_for(("google:gemini-2.5-flash", "anthropic:claude-haiku-4-5"))["temperature"] == 0


def test_reply_policy_default_and_validation(monkeypatch):
    monkeypatch.delenv("REPLY_POLICY", raising=False)
    assert Settings().reply_policy == "hold_for_approval"
    monkeypatch.setenv("REPLY_POLICY", "Auto_Send_Simple")
    assert Settings().reply_policy == "auto_send_simple"
    monkeypatch.setenv("REPLY_POLICY", "send_everything")
    with pytest.raises(ValueError, match="REPLY_POLICY"):
        Settings()
