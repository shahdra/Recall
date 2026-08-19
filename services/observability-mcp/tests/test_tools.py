"""Unit tests for the MCP tool surface.

These call the ``_``-prefixed logic functions directly — the same convention
study-mcp's tests follow — so the argument validation, S3 reading and Prometheus
parsing are exercised without going through the MCP transport. S3 is moto-mocked
and Prometheus is a stub, so nothing here needs a live cluster.
"""

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

import app
from conftest import BUCKET, k8s_record, put_log_object


def call(tool, **kwargs) -> dict:
    """Invoke a registered tool and parse its JSON envelope.

    Goes through the @mcp.tool wrapper so the envelope and error handling are
    covered too, not just the logic underneath.
    """
    return json.loads(tool(**kwargs))


# --- environment resolution ---------------------------------------------------


def test_unknown_env_is_a_clean_error(env):
    result = call(app.query_logs, env="staging")
    assert result["ok"] is False
    assert "staging" in result["error"]


def test_missing_bucket_names_the_variable(monkeypatch):
    """The error has to say which variable to set, or it is a puzzle."""
    monkeypatch.delenv("PROD_LOGS_BUCKET", raising=False)
    result = call(app.query_logs, env="prod")
    assert result["ok"] is False
    assert "PROD_LOGS_BUCKET" in result["error"]


def test_env_config_defaults_namespace_to_env_name(env):
    assert app._env_config("prod")["namespace"] == "prod"
    assert app._env_config("dev")["namespace"] == "dev"


# --- argument validation ------------------------------------------------------


@pytest.mark.parametrize("minutes", [0, -5, 1441, 99999])
def test_query_logs_rejects_out_of_range_lookback(env, minutes):
    result = call(app.query_logs, env="prod", since_minutes=minutes)
    assert result["ok"] is False
    assert "since_minutes" in result["error"]


@pytest.mark.parametrize("limit", [0, -1, 2001])
def test_query_logs_rejects_out_of_range_limit(env, limit):
    result = call(app.query_logs, env="prod", limit=limit)
    assert result["ok"] is False
    assert "limit" in result["error"]


def test_query_logs_at_rejects_bad_timestamp(env):
    result = call(app.query_logs_at, timestamp="yesterday-ish", env="prod")
    assert result["ok"] is False
    assert "ISO-8601" in result["error"]


def test_query_logs_at_rejects_wide_window(env):
    result = call(app.query_logs_at,
                  timestamp="2026-08-19T14:00:00Z", env="prod", window_minutes=500)
    assert result["ok"] is False
    assert "window_minutes" in result["error"]


# --- query_logs ---------------------------------------------------------------


def test_query_logs_returns_matching_lines(s3, env, now):
    put_log_object(s3, [
        k8s_record("agent started", service="tutor-agent"),
        k8s_record("card graded", service="tutor-agent"),
    ], when=now)

    result = call(app.query_logs, env="prod", since_minutes=10)
    assert result["ok"] is True
    assert result["matched"] == 2
    assert {line["log"] for line in result["lines"]} == {"agent started", "card graded"}
    assert result["lines"][0]["service"] == "tutor-agent"


def test_query_logs_filters_by_service(s3, env, now):
    put_log_object(s3, [
        k8s_record("from the agent", service="tutor-agent"),
        k8s_record("from the mcp", service="study-mcp"),
    ], when=now)

    result = call(app.query_logs, env="prod", service="study-mcp", since_minutes=10)
    assert result["matched"] == 1
    assert result["lines"][0]["log"] == "from the mcp"


def test_query_logs_accepts_a_pod_name_as_service(s3, env, now):
    """Pasting a pod name from kubectl should work without hand-trimming it."""
    put_log_object(s3, [k8s_record("hello", service="tutor-agent")], when=now)
    result = call(app.query_logs, env="prod",
                  service="tutor-agent-7d4f8b9c6-x2k9p", since_minutes=10)
    assert result["matched"] == 1


def test_query_logs_keeps_the_newest_when_trimming(s3, env, now):
    """The end of the window is where the failure is; trimming must keep it."""
    records = [
        k8s_record(f"line {i}", when=now - timedelta(seconds=60 - i))
        for i in range(10)
    ]
    put_log_object(s3, records, when=now)

    result = call(app.query_logs, env="prod", since_minutes=10, limit=3)
    assert result["returned"] == 3
    assert result["truncated_lines"] is True
    assert [line["log"] for line in result["lines"]] == ["line 7", "line 8", "line 9"]


def test_query_logs_excludes_records_outside_the_window(s3, env, now):
    """Objects are pre-filtered loosely, so per-record filtering must be exact."""
    put_log_object(s3, [
        k8s_record("in window", when=now - timedelta(minutes=2)),
        k8s_record("way too old", when=now - timedelta(hours=20)),
    ], when=now)

    result = call(app.query_logs, env="prod", since_minutes=5)
    assert [line["log"] for line in result["lines"]] == ["in window"]


def test_query_logs_empty_is_success_not_error(s3, env):
    """No logs is a valid answer; reporting it as failure sends people hunting a
    bug in the pipeline that is not there."""
    result = call(app.query_logs, env="prod", since_minutes=5)
    assert result["ok"] is True
    assert result["matched"] == 0
    assert result["lines"] == []


# --- query_logs_at ------------------------------------------------------------


def test_query_logs_at_searches_both_sides_of_the_moment(s3, env, now):
    """A window around a timestamp returns records either side of it, and drops
    ones outside.

    The incident is placed at ~now because moto stamps LastModified with the real
    upload time. That matches production: Fluent Bit uploads an object when it
    fills or times out, so an object's LastModified tracks when its records were
    written. Back-dating only the key would test a situation that cannot occur.
    """
    incident = now - timedelta(minutes=1)
    put_log_object(s3, [
        k8s_record("just before", when=incident - timedelta(minutes=2)),
        k8s_record("just after", when=incident + timedelta(minutes=2)),
        k8s_record("far away", when=incident - timedelta(minutes=45)),
    ], when=incident)

    result = call(app.query_logs_at, env="prod",
                  timestamp=incident.isoformat(), window_minutes=5)
    got = {line["log"] for line in result["lines"]}
    assert got == {"just before", "just after"}


# --- log_activity -------------------------------------------------------------


def test_log_activity_counts_by_service_and_namespace(s3, env, now):
    put_log_object(s3, [
        k8s_record("a", service="tutor-agent"),
        k8s_record("b", service="tutor-agent"),
        k8s_record("c", service="study-mcp"),
    ], when=now)

    result = call(app.log_activity, env="prod", since_minutes=10)
    assert result["ok"] is True
    assert result["services"] == {"tutor-agent": 2, "study-mcp": 1}
    assert result["namespaces"] == {"prod": 3}
    assert result["records"] == 3
    assert result["objects"] == 1


def test_log_activity_reports_silence_clearly(s3, env):
    """Distinguishing 'nothing logged' from 'pipeline broken' is the point of
    this tool, so an empty result must still be ok=True with zero counts."""
    result = call(app.log_activity, env="prod", since_minutes=10)
    assert result["ok"] is True
    assert result["records"] == 0
    assert result["services"] == {}


# --- Prometheus tools ---------------------------------------------------------


@pytest.fixture
def fake_prometheus(monkeypatch):
    """Intercept httpx.get and serve canned Prometheus payloads."""
    calls = []

    def handler(url, params=None, timeout=None):
        calls.append({"url": url, "params": params or {}})
        if "/api/v1/query_range" in url:
            payload = {"status": "success", "data": {"result": [
                {"metric": {"__name__": "up"}, "values": [[1755612000, "1"]]}]}}
        elif "/api/v1/query" in url:
            payload = {"status": "success", "data": {"result": [
                {"metric": {"__name__": "up", "job": "tutor-agent"}, "value": [1755612000, "1"]}]}}
        elif "/api/v1/label/__name__/values" in url:
            payload = {"status": "success", "data": [
                "up", "http_requests_total",
                "recall_llm_failures_total", "recall_quiz_correct_total"]}
        elif "/api/v1/alerts" in url:
            payload = {"status": "success", "data": {"alerts": [
                {"labels": {"alertname": "RecallLLMFailures", "severity": "critical",
                            "namespace": "prod"},
                 "state": "firing", "activeAt": "2026-08-19T14:00:00Z",
                 "annotations": {"summary": "Model invocations failing in prod"}},
                {"labels": {"alertname": "RecallAgentCapped", "severity": "warning",
                            "namespace": "prod"},
                 "state": "pending", "activeAt": "2026-08-19T14:05:00Z",
                 "annotations": {"summary": "Agent runs hitting the cap"}},
                {"labels": {"alertname": "SomeDevAlert", "severity": "warning",
                            "namespace": "dev"},
                 "state": "firing", "activeAt": "2026-08-19T14:05:00Z",
                 "annotations": {}},
            ]}}
        else:
            payload = {"status": "error", "error": "unexpected path"}

        return httpx.Response(200, json=payload,
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(app.httpx, "get", handler)
    return calls


def test_query_metrics_returns_the_result_series(env, fake_prometheus):
    result = call(app.query_metrics, env="prod", promql="up")
    assert result["ok"] is True
    assert result["result"][0]["metric"]["job"] == "tutor-agent"
    assert fake_prometheus[0]["params"]["query"] == "up"


def test_query_metrics_range_sends_a_window_and_step(env, fake_prometheus):
    result = call(app.query_metrics_range, env="prod", promql="up",
                  since_minutes=30, step_seconds=60)
    assert result["ok"] is True
    params = fake_prometheus[0]["params"]
    assert params["step"] == 60
    assert params["end"] - params["start"] == pytest.approx(1800, abs=2)


@pytest.mark.parametrize("step", [1, 4, 3601])
def test_query_metrics_range_rejects_bad_step(env, fake_prometheus, step):
    result = call(app.query_metrics_range, env="prod", step_seconds=step)
    assert result["ok"] is False
    assert "step_seconds" in result["error"]


def test_list_metrics_filters_by_prefix(env, fake_prometheus):
    """The cluster exports thousands of series; the prefix is what makes the
    result readable."""
    result = call(app.list_metrics, env="prod", prefix="recall_")
    assert result["ok"] is True
    assert result["count"] == 2
    assert all(name.startswith("recall_") for name in result["metrics"])


def test_list_metrics_without_prefix_returns_everything(env, fake_prometheus):
    result = call(app.list_metrics, env="prod")
    assert result["count"] == 4


def test_check_alerts_returns_only_firing_by_default(env, fake_prometheus):
    result = call(app.check_alerts, env="prod")
    assert result["count"] == 1
    assert result["alerts"][0]["name"] == "RecallLLMFailures"
    assert result["alerts"][0]["severity"] == "critical"


def test_check_alerts_can_include_pending(env, fake_prometheus):
    result = call(app.check_alerts, env="prod", firing_only=False)
    names = {a["name"] for a in result["alerts"]}
    assert names == {"RecallLLMFailures", "RecallAgentCapped"}


def test_check_alerts_filters_to_the_requested_namespace(env, fake_prometheus):
    """One Prometheus watches both namespaces, so a prod question must not
    return a dev alert."""
    result = call(app.check_alerts, env="prod", firing_only=False)
    assert all(a["name"] != "SomeDevAlert" for a in result["alerts"])


def test_prometheus_failure_becomes_an_error_envelope(env, monkeypatch):
    """A tool must never raise at the transport: the caller is an LLM that can
    only read the text it gets back."""
    def boom(url, params=None, timeout=None):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(app.httpx, "get", boom)
    result = call(app.query_metrics, env="prod", promql="up")
    assert result["ok"] is False
    assert "connection refused" in result["error"]


def test_missing_prometheus_url_names_the_variable(monkeypatch):
    monkeypatch.delenv("PROD_PROMETHEUS_URL", raising=False)
    result = call(app.query_metrics, env="prod")
    assert result["ok"] is False
    assert "PROD_PROMETHEUS_URL" in result["error"]


# --- tool registration --------------------------------------------------------


def _registered():
    """The server's advertised tools, via whichever accessor this FastMCP has."""
    import asyncio
    listed = app.mcp.list_tools()
    if asyncio.iscoroutine(listed):
        listed = asyncio.get_event_loop().run_until_complete(listed)
    return listed


def test_all_seven_tools_are_registered():
    """The count is the contract: an editor shows what the server advertises."""
    names = {t.name for t in _registered()}
    assert names == {
        "query_logs", "query_logs_at", "log_activity",
        "query_metrics", "query_metrics_range", "list_metrics", "check_alerts",
    }


def test_every_tool_has_a_description():
    """The docstring becomes the tool's description over the wire — without it
    the model has to guess what the tool does."""
    for tool in _registered():
        assert tool.description, f"{tool.name} has no description"
