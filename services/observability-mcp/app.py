"""observability-mcp: Recall's log and metric tools, exposed over MCP.

Recall has two MCP servers, and they are deliberately different shapes.
``study-mcp`` runs *in* the deployment over HTTP because the tutor-agent calls it
on every request. This one runs on a developer's machine over **stdio**, because
its consumer is a person debugging an incident, not the agent serving traffic.

That split is the whole design. Log search is an operator tool: it reads
production data, it is slow enough to be interactive rather than in-request, and
nothing about a tutoring turn needs it. Deploying it beside the app would put a
"read all production logs" surface inside the request path for no benefit.

Tools:
    query_logs                what happened in the last N minutes
    query_logs_at             what happened around a specific timestamp
    log_activity              which services are shipping logs at all
    query_metrics             instant PromQL
    query_metrics_range       ranged PromQL, for a trend
    list_metrics              metric names, for writing the query above
    check_alerts             which Recall alerts are firing right now

Every tool returns a JSON string and never raises: an exception here surfaces in
an editor as a broken tool rather than an answer, so failures come back as
``{"ok": false, "error": …}`` instead.
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import boto3
import httpx
from fastmcp import FastMCP

import logs

logger = logging.getLogger(__name__)

mcp = FastMCP("recall-observability")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

MAX_MINUTES = 1440
"""24 hours. Past a day, the honest answer is a log-analytics query, not this."""

MAX_LIMIT = 2000
"""Lines returned. Enough to see a stack trace in context; small enough that the
response does not blow an editor's context window."""

MAX_WINDOW_MINUTES = 120
"""Half-width of an incident window. Two hours either side of a timestamp is
already a wide net for "what broke at 14:02"."""

DEFAULT_TIMEOUT = 30.0

_ENVIRONMENTS = ("dev", "prod")


def _env_config(env: str) -> dict:
    """Resolve one environment's Prometheus URL, log bucket, and namespace.

    Read per call rather than at import so a developer can export a variable and
    re-run without restarting the server their editor is holding open.
    """
    key = (env or "").strip().lower()
    if key not in _ENVIRONMENTS:
        raise ValueError(f"unknown env {env!r}; expected 'dev' or 'prod'")

    prefix = key.upper()
    return {
        "env": key,
        "prometheus": os.environ.get(f"{prefix}_PROMETHEUS_URL", ""),
        "bucket": os.environ.get(f"{prefix}_LOGS_BUCKET", ""),
        # Recall's namespaces are named for the environment, which is also what
        # the alert rules select on.
        "namespace": os.environ.get(f"{prefix}_NAMESPACE", key),
    }


def _require(config: dict, field: str, hint: str) -> str:
    value = config.get(field)
    if not value:
        raise ValueError(
            f"no {field} configured for env {config['env']!r} — set {hint}"
        )
    return value


def _s3():
    return boto3.client("s3", region_name=AWS_REGION)


def _check_range(name: str, value: int, low: int, high: int) -> int:
    value = int(value)
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


def _envelope(fn, **context):
    """Run a tool body, turning any failure into a JSON error envelope.

    Tools are called by an LLM, which recovers far better from a structured error
    than from a transport-level exception it cannot see the text of.
    """
    try:
        return json.dumps(fn(), default=str)
    except Exception as exc:  # noqa: BLE001 - the boundary; report, never raise
        logger.warning("tool failed: %s", exc, exc_info=True)
        return json.dumps({"ok": False, "error": str(exc), **context})


# --- log collection -----------------------------------------------------------


def _collect(env: str, service, start: datetime, end: datetime, limit: int) -> dict:
    """Gather matching log lines in a window into a result envelope."""
    config = _env_config(env)
    bucket = _require(config, "bucket", f"{env.upper()}_LOGS_BUCKET")

    wanted = logs.normalize_service(service) if service else None
    low, high = start.timestamp(), end.timestamp()

    client = _s3()
    _, truncated = logs.list_objects(client, bucket, start, end)

    collected = []
    for record in logs.iter_records(client, bucket, start, end):
        stamp = logs.record_ts(record)
        if stamp is not None and not (low <= stamp <= high):
            continue
        if wanted and not logs.matches_service(record, wanted):
            continue

        identity = logs.record_identity(record)
        collected.append({
            "time": (
                datetime.fromtimestamp(stamp, timezone.utc).isoformat()
                if stamp else None
            ),
            "service": identity["app"] or identity["container"],
            "pod": identity["pod"],
            "namespace": identity["namespace"],
            "stream": record.get("stream") or record.get("source"),
            "log": str(record.get("log", "")).rstrip("\n"),
        })

    collected.sort(key=lambda r: r["time"] or "")
    # Keep the NEWEST lines when trimming: the end of an incident window is where
    # the failure is, and silently keeping the oldest would hide it.
    kept = collected[-limit:]

    return {
        "ok": True,
        "env": config["env"],
        "service": service,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "matched": len(collected),
        "returned": len(kept),
        # Both truncation kinds are reported: a partial answer that looks
        # complete is worse than no answer.
        "truncated_objects": truncated,
        "truncated_lines": len(collected) > len(kept),
        "lines": kept,
    }


# --- tool logic ---------------------------------------------------------------
#
# Each tool below is a thin wrapper over a ``_``-prefixed function, the same shape
# study-mcp uses: unit tests call the logic directly, and the wrapper only adds
# the MCP registration and the JSON envelope.


def _query_logs(env, service, since_minutes, limit) -> dict:
    minutes = _check_range("since_minutes", since_minutes, 1, MAX_MINUTES)
    capped = _check_range("limit", limit, 1, MAX_LIMIT)
    end = datetime.now(timezone.utc)
    return _collect(env, service, end - timedelta(minutes=minutes), end, capped)


def _query_logs_at(timestamp, env, service, window_minutes, limit) -> dict:
    half = _check_range("window_minutes", window_minutes, 1, MAX_WINDOW_MINUTES)
    capped = _check_range("limit", limit, 1, MAX_LIMIT)

    centre = logs.record_ts({"time": timestamp})
    if centre is None:
        raise ValueError("timestamp must be ISO-8601, e.g. '2026-08-19T14:02:00Z'")

    middle = datetime.fromtimestamp(centre, timezone.utc)
    return _collect(
        env, service,
        middle - timedelta(minutes=half),
        middle + timedelta(minutes=half),
        capped,
    )


def _log_activity(env, since_minutes) -> dict:
    minutes = _check_range("since_minutes", since_minutes, 1, MAX_MINUTES)
    config = _env_config(env)
    bucket = _require(config, "bucket", f"{config['env'].upper()}_LOGS_BUCKET")

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=minutes)
    low, high = start.timestamp(), end.timestamp()

    client = _s3()
    objects, truncated = logs.list_objects(client, bucket, start, end)

    services: dict[str, int] = {}
    pods: dict[str, int] = {}
    namespaces: dict[str, int] = {}
    streams: dict[str, int] = {}
    total = 0

    for record in logs.iter_records(client, bucket, start, end):
        stamp = logs.record_ts(record)
        if stamp is not None and not (low <= stamp <= high):
            continue
        total += 1
        identity = logs.record_identity(record)
        for counter, value in (
            (services, identity["app"] or identity["container"]),
            (pods, identity["pod"]),
            (namespaces, identity["namespace"]),
            (streams, record.get("stream") or record.get("source")),
        ):
            if value:
                counter[value] = counter.get(value, 0) + 1

    return {
        "ok": True,
        "env": config["env"],
        "since_minutes": minutes,
        "objects": len(objects),
        "truncated_objects": truncated,
        "records": total,
        "services": services,
        "pods": pods,
        "namespaces": namespaces,
        "streams": streams,
    }


def _prometheus_get(config: dict, path: str, params: dict) -> dict:
    base = _require(config, "prometheus", f"{config['env'].upper()}_PROMETHEUS_URL")
    response = httpx.get(
        f"{base.rstrip('/')}{path}", params=params, timeout=DEFAULT_TIMEOUT
    )
    response.raise_for_status()
    return response.json()


def _query_metrics(env, promql) -> dict:
    config = _env_config(env)
    payload = _prometheus_get(config, "/api/v1/query", {"query": promql})
    return {
        "ok": payload.get("status") == "success",
        "env": config["env"],
        "query": promql,
        "result": payload.get("data", {}).get("result", []),
        "error": payload.get("error"),
    }


def _query_metrics_range(env, promql, since_minutes, step_seconds) -> dict:
    minutes = _check_range("since_minutes", since_minutes, 1, MAX_MINUTES)
    step = _check_range("step_seconds", step_seconds, 5, 3600)
    config = _env_config(env)
    end = datetime.now(timezone.utc).timestamp()
    payload = _prometheus_get(config, "/api/v1/query_range", {
        "query": promql,
        "start": end - minutes * 60,
        "end": end,
        "step": step,
    })
    return {
        "ok": payload.get("status") == "success",
        "env": config["env"],
        "query": promql,
        "result": payload.get("data", {}).get("result", []),
        "error": payload.get("error"),
    }


def _list_metrics(env, prefix) -> dict:
    config = _env_config(env)
    payload = _prometheus_get(config, "/api/v1/label/__name__/values", {})
    names = payload.get("data", []) or []
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    return {
        "ok": payload.get("status") == "success",
        "env": config["env"],
        "prefix": prefix or None,
        "count": len(names),
        "metrics": sorted(names),
    }


def _check_alerts(env, firing_only) -> dict:
    config = _env_config(env)
    payload = _prometheus_get(config, "/api/v1/alerts", {})
    alerts = payload.get("data", {}).get("alerts", []) or []

    namespace = config["namespace"]
    selected = []
    for alert in alerts:
        labels = alert.get("labels", {}) or {}
        # One Prometheus watches every namespace, so a prod question must not
        # return a dev alert.
        if labels.get("namespace") not in (None, "", namespace):
            continue
        if firing_only and alert.get("state") != "firing":
            continue
        selected.append({
            "name": labels.get("alertname"),
            "state": alert.get("state"),
            "severity": labels.get("severity"),
            "since": alert.get("activeAt"),
            "summary": (alert.get("annotations", {}) or {}).get("summary"),
        })

    return {
        "ok": payload.get("status") == "success",
        "env": config["env"],
        "firing_only": firing_only,
        "count": len(selected),
        "alerts": selected,
    }


# --- MCP tool surface ---------------------------------------------------------


@mcp.tool
def query_logs(
    env: str = "prod",
    service: str | None = None,
    since_minutes: int = 15,
    limit: int = 200,
) -> str:
    """Recent container logs for a Recall service, read from the S3 log archive.

    env: 'dev' or 'prod'. service: 'tutor-agent', 'study-mcp', 'frontend',
    'reminder' — omit for everything. since_minutes: 1-1440. limit: 1-2000.
    Matches on Kubernetes labels, falling back to the log text.
    """
    return _envelope(
        lambda: _query_logs(env, service, since_minutes, limit),
        env=env, service=service,
    )


@mcp.tool
def query_logs_at(
    timestamp: str,
    env: str = "prod",
    service: str | None = None,
    window_minutes: int = 10,
    limit: int = 300,
) -> str:
    """Logs from around a specific moment — for investigating an alert or incident.

    timestamp: ISO-8601, e.g. '2026-08-19T14:02:00Z'. Searches the window either
    side of it, so pass the time an alert fired. window_minutes: 1-120.
    """
    return _envelope(
        lambda: _query_logs_at(timestamp, env, service, window_minutes, limit),
        env=env, timestamp=timestamp,
    )


@mcp.tool
def log_activity(env: str = "prod", since_minutes: int = 60) -> str:
    """Which services, pods and namespaces are shipping logs, with line counts.

    Answers 'is anything logging at all?' — the first question when logs look
    empty, because it separates "no errors" from "no pipeline".
    """
    return _envelope(lambda: _log_activity(env, since_minutes), env=env)


@mcp.tool
def query_metrics(env: str = "prod", promql: str = "up") -> str:
    """Run an instant PromQL query against an environment's Prometheus.

    Recall's own series are prefixed 'recall_': recall_llm_failures_total,
    recall_llm_fallbacks_total, recall_tool_errors_total, recall_agent_capped_total,
    recall_agent_iterations, recall_quizzes_graded_total, recall_quiz_correct_total,
    recall_cards_generated_total, recall_decks_created_total,
    recall_transcription_failures_total.
    """
    return _envelope(lambda: _query_metrics(env, promql), env=env, query=promql)


@mcp.tool
def query_metrics_range(
    env: str = "prod",
    promql: str = "up",
    since_minutes: int = 30,
    step_seconds: int = 30,
) -> str:
    """Run a ranged PromQL query — use when the shape over time is the answer.

    Accuracy, for instance, is not stored as a gauge: derive it as
    'rate(recall_quiz_correct_total[5m]) / rate(recall_quizzes_graded_total[5m])'.
    since_minutes: 1-1440. step_seconds: 5-3600.
    """
    return _envelope(
        lambda: _query_metrics_range(env, promql, since_minutes, step_seconds),
        env=env, query=promql,
    )


@mcp.tool
def list_metrics(env: str = "prod", prefix: str = "") -> str:
    """List metric names, optionally filtered by prefix.

    Call with prefix='recall_' to see only Recall's own instrumentation rather
    than the several thousand series the cluster exporters publish.
    """
    return _envelope(lambda: _list_metrics(env, prefix), env=env)


@mcp.tool
def check_alerts(env: str = "prod", firing_only: bool = True) -> str:
    """Which alerts are currently pending or firing.

    The natural first call when something looks wrong: it names the rule, which
    then tells you which service and window to pull logs for.
    """
    return _envelope(lambda: _check_alerts(env, firing_only), env=env)


if __name__ == "__main__":
    # stdio, not HTTP: an editor launches this process and speaks MCP over
    # stdin/stdout. study-mcp is the one that listens on a port.
    mcp.run()
