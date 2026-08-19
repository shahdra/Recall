"""Unit tests for the log parsing and S3 reading layer.

These cover the shapes that make real log ingestion awkward: records wrapped
twice, nanosecond timestamps, windows that cross midnight, and pod names with
generated suffixes.
"""

from datetime import datetime, timedelta, timezone

import pytest

import logs
from conftest import BUCKET, k8s_record, put_log_object


# --- day_prefixes -------------------------------------------------------------


def test_day_prefixes_single_day():
    start = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 19, 14, 0, tzinfo=timezone.utc)
    assert logs.day_prefixes(start, end) == ["logs/2026/08/19/"]


def test_day_prefixes_spans_midnight():
    """A window crossing midnight must list both days, or the earlier half of an
    overnight incident is invisible."""
    start = datetime(2026, 8, 19, 23, 30, tzinfo=timezone.utc)
    end = datetime(2026, 8, 20, 0, 30, tzinfo=timezone.utc)
    assert logs.day_prefixes(start, end) == ["logs/2026/08/19/", "logs/2026/08/20/"]


def test_day_prefixes_pads_single_digits():
    """Fluent Bit zero-pads; an unpadded prefix silently matches nothing."""
    start = end = datetime(2026, 1, 5, 12, 0, tzinfo=timezone.utc)
    assert logs.day_prefixes(start, end) == ["logs/2026/01/05/"]


# --- unwrap -------------------------------------------------------------------


def test_unwrap_flattens_nested_log():
    record = k8s_record("boom", nested=True)
    flat = logs.unwrap(record)
    assert flat["log"] == "boom"
    assert flat["kubernetes"]["labels"]["app"] == "tutor-agent"
    # The outer envelope's own fields survive.
    assert flat["host"] == "ip-10-0-1-42"
    assert flat["_outer_date"] == record["date"]


def test_unwrap_is_noop_on_flat_record():
    record = k8s_record("already flat", nested=False)
    assert logs.unwrap(record)["log"] == "already flat"


def test_unwrap_leaves_plain_text_alone():
    """A log line that merely starts with a brace is not JSON."""
    record = {"log": "{not actually json"}
    assert logs.unwrap(record) == record


def test_unwrap_tolerates_non_dict_json():
    """`log` holding a JSON array must not be merged as if it were fields."""
    record = {"log": "[1, 2, 3]"}
    assert logs.unwrap(record) == record


# --- record_ts ----------------------------------------------------------------


def test_record_ts_parses_nanosecond_precision():
    """The container runtime writes 9 fractional digits; fromisoformat takes 6.

    This is the specific parse that fails if the trimming is removed.
    """
    ts = logs.record_ts({"time": "2026-08-19T14:02:03.123456789Z"})
    assert ts is not None
    assert datetime.fromtimestamp(ts, timezone.utc).year == 2026


def test_record_ts_accepts_numeric_date():
    assert logs.record_ts({"date": 1755612123.5}) == 1755612123.5


def test_record_ts_handles_explicit_offset():
    a = logs.record_ts({"time": "2026-08-19T14:02:03.123456789+00:00"})
    b = logs.record_ts({"time": "2026-08-19T14:02:03.123456Z"})
    assert a is not None and b is not None
    assert abs(a - b) < 0.001


def test_record_ts_assumes_utc_when_naive():
    naive = logs.record_ts({"time": "2026-08-19T14:02:03"})
    aware = logs.record_ts({"time": "2026-08-19T14:02:03Z"})
    assert naive == aware


def test_record_ts_returns_none_without_a_time():
    assert logs.record_ts({"log": "no timestamp here"}) is None


def test_record_ts_returns_none_on_garbage():
    assert logs.record_ts({"time": "not-a-date"}) is None


# --- normalize_service --------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("tutor-agent", "tutor-agent"),
    ("tutor-agent-7d4f8b9c6-x2k9p", "tutor-agent"),
    ("study-mcp", "study-mcp"),
    ("study_mcp", "study-mcp"),
    ("/frontend", "frontend"),
    ("TUTOR-AGENT", "tutor-agent"),
    ("reminder-service", "reminder"),
])
def test_normalize_service(name, expected):
    assert logs.normalize_service(name) == expected


def test_normalize_service_keeps_meaningful_words():
    """Stripping must not eat a real segment: 'mcp' has no digit, so it stays."""
    assert logs.normalize_service("study-mcp") == "study-mcp"


# --- record_identity ----------------------------------------------------------


def test_record_identity_reads_kubernetes_metadata():
    flat = logs.unwrap(k8s_record("x", service="study-mcp", namespace="dev"))
    identity = logs.record_identity(flat)
    assert identity["app"] == "study-mcp"
    assert identity["container"] == "study-mcp"
    assert identity["namespace"] == "dev"
    assert identity["pod"].startswith("study-mcp-")


def test_record_identity_tolerates_missing_metadata():
    identity = logs.record_identity({"log": "bare line"})
    assert identity["app"] is None
    assert identity["pod"] is None


# --- matches_service ----------------------------------------------------------


def test_matches_service_by_label():
    flat = logs.unwrap(k8s_record("x", service="tutor-agent"))
    assert logs.matches_service(flat, "tutor-agent")
    assert not logs.matches_service(flat, "study-mcp")


def test_matches_service_falls_back_to_text_without_metadata():
    """A record with no Kubernetes fields is searched by text, so it is never
    silently dropped from a search."""
    assert logs.matches_service({"log": "study-mcp timed out"}, "study-mcp")
    assert not logs.matches_service({"log": "unrelated"}, "study-mcp")


def test_matches_service_does_not_text_match_when_metadata_exists():
    """A tutor-agent record mentioning study-mcp belongs to tutor-agent.

    Without this, searching one service returns every service that logged its
    name — which is most of them.
    """
    flat = logs.unwrap(k8s_record("calling study-mcp", service="tutor-agent"))
    assert not logs.matches_service(flat, "study-mcp")


# --- list_objects / iter_records ---------------------------------------------


def test_list_objects_filters_by_window(s3, now):
    put_log_object(s3, [k8s_record("recent")], when=now)
    put_log_object(s3, [k8s_record("old")], when=now - timedelta(days=3))

    found, truncated = logs.list_objects(
        s3, BUCKET, now - timedelta(minutes=10), now + timedelta(minutes=1)
    )
    assert len(found) == 1
    assert not truncated


def test_list_objects_margin_catches_boundary_records(s3, now):
    """An object uploaded just after the window still holds records from inside
    it, so the margin must include it."""
    put_log_object(s3, [k8s_record("edge")], when=now + timedelta(seconds=45))
    found, _ = logs.list_objects(s3, BUCKET, now - timedelta(minutes=5), now)
    assert len(found) == 1


def test_iter_records_reads_gzipped_ndjson(s3, now):
    put_log_object(s3, [k8s_record("first"), k8s_record("second")], when=now)
    records = list(logs.iter_records(
        s3, BUCKET, now - timedelta(minutes=5), now + timedelta(minutes=1)))
    assert [r["log"] for r in records] == ["first", "second"]


def test_iter_records_reads_uncompressed_object(s3, now):
    """A hand-written plain object should still be readable."""
    put_log_object(s3, [k8s_record("plain")], when=now, gzipped=False)
    records = list(logs.iter_records(
        s3, BUCKET, now - timedelta(minutes=5), now + timedelta(minutes=1)))
    assert records[0]["log"] == "plain"


def test_iter_records_survives_a_malformed_line(s3, now):
    """One bad line must not lose the rest of the object."""
    import gzip as _gzip, json as _json
    body = _gzip.compress(
        (_json.dumps(k8s_record("good one")) + "\nNOT JSON AT ALL\n").encode())
    s3.put_object(
        Bucket=BUCKET,
        Key=f"logs/{now.year:04d}/{now.month:02d}/{now.day:02d}/kube_x.gz",
        Body=body,
    )
    records = list(logs.iter_records(
        s3, BUCKET, now - timedelta(minutes=5), now + timedelta(minutes=1)))
    messages = [r.get("log") for r in records]
    assert "good one" in messages
    assert "NOT JSON AT ALL" in messages
