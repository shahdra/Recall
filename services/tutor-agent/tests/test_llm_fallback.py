"""Tests for the invoke-time fallback.

Init-time fallback alone is not enough: Bedrock constructs a chat model lazily,
so a denied model, a bad id, or a provider outage all surface on the first
*invoke*, not at construction. Without this wrapper the fallback would never
fire in the very situation it exists for.
"""

import pytest

from fakes import FakeMessage
from llm import FallbackChatModel


class Flaky:
    """Fails its first ``fail_times`` invokes, then succeeds."""

    def __init__(self, name, fail_times=0, error=None):
        self.name = name
        self.fail_times = fail_times
        self.error = error or RuntimeError(f"{name} unavailable")
        self.invokes = 0
        self.bound = None

    def invoke(self, messages, **kwargs):
        self.invokes += 1
        if self.invokes <= self.fail_times:
            raise self.error
        return FakeMessage(content=f"{self.name} answered")

    def bind_tools(self, tools, **kwargs):
        self.bound = tools
        return self


def test_primary_success_never_touches_secondary():
    primary, secondary = Flaky("primary"), Flaky("secondary")
    model = FallbackChatModel(primary, secondary, retries=1)
    assert model.invoke("hi").content == "primary answered"
    assert secondary.invokes == 0


def test_retries_primary_before_falling_back():
    primary = Flaky("primary", fail_times=1)
    secondary = Flaky("secondary")
    model = FallbackChatModel(primary, secondary, retries=2, backoff_seconds=0)
    assert model.invoke("hi").content == "primary answered"
    assert primary.invokes == 2
    assert secondary.invokes == 0


def test_falls_back_after_primary_exhausts_retries():
    primary = Flaky("primary", fail_times=99)
    secondary = Flaky("secondary")
    model = FallbackChatModel(primary, secondary, retries=2, backoff_seconds=0)
    assert model.invoke("hi").content == "secondary answered"
    assert primary.invokes == 2
    assert secondary.invokes == 1


def test_raises_when_both_fail():
    primary = Flaky("primary", fail_times=99)
    secondary = Flaky("secondary", fail_times=99)
    model = FallbackChatModel(primary, secondary, retries=1, backoff_seconds=0)
    with pytest.raises(Exception):
        model.invoke("hi")


def test_counts_failures_and_fallbacks_for_metrics():
    primary = Flaky("primary", fail_times=99)
    secondary = Flaky("secondary")
    model = FallbackChatModel(primary, secondary, retries=2, backoff_seconds=0)
    model.invoke("hi")
    assert model.failure_count == 2
    assert model.fallback_count == 1


def test_bind_tools_binds_both_models():
    """Whichever model answers must know about the same tools."""
    primary, secondary = Flaky("primary"), Flaky("secondary")
    model = FallbackChatModel(primary, secondary, retries=1)
    bound = model.bind_tools(["toolA"])
    assert primary.bound == ["toolA"]
    assert secondary.bound == ["toolA"]


def test_bind_tools_does_not_mutate_the_original():
    """The app shares one model between the orchestrator and both sub-agents.

    If bind_tools mutated in place, the orchestrator's tools would leak into the
    Card-Generator, which then sends Bedrock a card-generation prompt with tool
    definitions attached and gets an InternalFailure. One /chat call would
    permanently degrade every later /decks call in the same process.
    """

    class Marker:
        def __init__(self, tag):
            self.tag = tag

        def bind_tools(self, tools, **kwargs):
            return Marker(f"{self.tag}+bound")

        def invoke(self, messages, **kwargs):
            return FakeMessage(content=self.tag)

    model = FallbackChatModel(Marker("plain"), Marker("plain2"), retries=1)
    bound = model.bind_tools(["toolA"])

    assert bound is not model
    assert model.invoke("hi").content == "plain"  # original still unbound
    assert bound.invoke("hi").content == "plain+bound"


def test_bound_copy_shares_failure_counters():
    """Metrics read the original, so a bound copy's failures must still register."""
    primary = Flaky("primary", fail_times=99)
    secondary = Flaky("secondary")
    model = FallbackChatModel(primary, secondary, retries=1, backoff_seconds=0)
    bound = model.bind_tools([])
    bound.invoke("hi")
    assert model.failure_count >= 1
    assert model.fallback_count == 1


def test_works_with_no_secondary_configured():
    primary = Flaky("primary", fail_times=1)
    model = FallbackChatModel(primary, None, retries=2, backoff_seconds=0)
    assert model.invoke("hi").content == "primary answered"


def test_no_secondary_and_primary_dead_raises():
    primary = Flaky("primary", fail_times=99)
    model = FallbackChatModel(primary, None, retries=1, backoff_seconds=0)
    with pytest.raises(Exception):
        model.invoke("hi")


def test_build_llm_returns_a_fallback_wrapper():
    from llm import build_llm

    model = build_llm("a", "b", init=lambda m, **k: Flaky(m))
    assert isinstance(model, FallbackChatModel)


def test_build_llm_without_fallback_still_wraps_for_retries():
    from llm import build_llm

    model = build_llm("a", None, init=lambda m, **k: Flaky(m))
    assert isinstance(model, FallbackChatModel)
    assert model.secondary is None
