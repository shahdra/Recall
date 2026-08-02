import pytest

from llm import DEFAULT_FALLBACK_MODEL, DEFAULT_MODEL, build_llm


class Recorder:
    """Records which model ids were attempted, failing the ones told to."""

    def __init__(self, failing=()):
        self.failing = set(failing)
        self.attempted = []

    def __call__(self, model, **kwargs):
        self.attempted.append(model)
        if model in self.failing:
            raise RuntimeError(f"cannot init {model}")
        return f"model<{model}>"


def test_primary_is_used_when_it_works():
    init = Recorder()
    llm = build_llm("primary", "secondary", init=init)
    assert llm.primary == "model<primary>"
    # Both are constructed up front so the secondary is ready mid-session;
    # Bedrock init is lazy and cheap, so this costs nothing until invoked.
    assert init.attempted == ["primary", "secondary"]


def test_falls_back_when_primary_fails():
    init = Recorder(failing=["primary"])
    llm = build_llm("primary", "secondary", init=init)
    assert llm.primary == "model<secondary>"
    assert llm.secondary is None  # nothing left to fall back to
    assert init.attempted == ["primary", "secondary"]


def test_unavailable_fallback_does_not_block_startup():
    """A working primary should serve traffic even with no safety net."""
    init = Recorder(failing=["secondary"])
    llm = build_llm("primary", "secondary", init=init)
    assert llm.primary == "model<primary>"
    assert llm.secondary is None


def test_raises_when_both_fail():
    init = Recorder(failing=["primary", "secondary"])
    with pytest.raises(RuntimeError):
        build_llm("primary", "secondary", init=init)


def test_raises_when_primary_fails_and_no_fallback_configured():
    init = Recorder(failing=["primary"])
    with pytest.raises(RuntimeError):
        build_llm("primary", None, init=init)


def test_temperature_zero_is_requested():
    """Grading and card generation must be reproducible."""
    captured = {}

    def init(model, **kwargs):
        captured.update(kwargs)
        return "m"

    build_llm("primary", None, init=init)
    assert captured.get("temperature") == 0


def test_defaults_point_at_allowlisted_bedrock_models():
    """The course IAM policy denies anything outside its allowlist."""
    assert DEFAULT_MODEL == "bedrock:amazon.nova-lite-v1:0"
    assert DEFAULT_FALLBACK_MODEL == (
        "bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0"
    )


def test_fallback_is_a_different_provider_than_primary():
    """A same-provider fallback would share the outage it exists to survive."""
    assert "nova" in DEFAULT_MODEL
    assert "nova" not in DEFAULT_FALLBACK_MODEL


def test_error_message_names_both_models():
    init = Recorder(failing=["primary", "secondary"])
    with pytest.raises(RuntimeError) as exc:
        build_llm("primary", "secondary", init=init)
    assert "primary" in str(exc.value)
    assert "secondary" in str(exc.value)


def test_reads_models_from_environment(monkeypatch):
    monkeypatch.setenv("MODEL", "env-primary")
    monkeypatch.setenv("FALLBACK_MODEL", "env-secondary")
    init = Recorder()
    build_llm(init=init)
    assert init.attempted == ["env-primary", "env-secondary"]


def test_uses_defaults_when_environment_is_unset(monkeypatch):
    monkeypatch.delenv("MODEL", raising=False)
    monkeypatch.delenv("FALLBACK_MODEL", raising=False)
    init = Recorder()
    build_llm(init=init)
    assert init.attempted == [DEFAULT_MODEL, DEFAULT_FALLBACK_MODEL]
