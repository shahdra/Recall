"""Shared fixtures: a moto-mocked log bucket holding real Fluent Bit record shapes.

The records here mirror what the DaemonSet actually ships — including the
doubly-encoded `log` field and nanosecond timestamps — because those shapes are
the whole reason the parsing code is not a one-liner.
"""

import gzip
import json
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

REGION = "us-east-1"
BUCKET = "shahdra-recall-logs-prod"


def k8s_record(message, *, service="tutor-agent", pod=None, namespace="prod",
               when=None, stream="stdout", nested=True):
    """One log record as Fluent Bit's kubernetes filter emits it.

    nested=True reproduces the doubly-encoded form (the runtime's JSON line
    wrapped in Fluent Bit's envelope); nested=False the already-flat form. Both
    occur in practice depending on the parser, so both are tested.
    """
    when = when or datetime.now(timezone.utc)
    pod = pod or f"{service}-7d4f8b9c6-x2k9p"
    inner = {
        "log": message,
        "stream": stream,
        # Nanosecond precision: datetime.fromisoformat cannot parse this directly.
        "time": when.strftime("%Y-%m-%dT%H:%M:%S.") + f"{when.microsecond:06d}000Z",
        "kubernetes": {
            "pod_name": pod,
            "container_name": service,
            "namespace_name": namespace,
            "labels": {"app": service},
        },
    }
    if nested:
        return {"date": when.timestamp(), "host": "ip-10-0-1-42",
                "log": json.dumps(inner)}
    return {"date": when.timestamp(), "host": "ip-10-0-1-42", **inner}


def put_log_object(s3, records, *, when=None, bucket=BUCKET, gzipped=True):
    """Write records as one gzipped NDJSON object under the Fluent Bit key format."""
    when = when or datetime.now(timezone.utc)
    body = "\n".join(json.dumps(r) for r in records).encode()
    if gzipped:
        body = gzip.compress(body)
    key = (f"logs/{when.year:04d}/{when.month:02d}/{when.day:02d}/"
           f"kube_{when.strftime('%H%M%S')}_{abs(hash(str(records))) % 10000}.gz")
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    return key


@pytest.fixture
def s3():
    """A mocked S3 with the log bucket created."""
    with mock_aws():
        client = boto3.client("s3", region_name=REGION)
        client.create_bucket(Bucket=BUCKET)
        yield client


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


@pytest.fixture
def env(monkeypatch):
    """Point the server at the mocked bucket and a fake Prometheus."""
    monkeypatch.setenv("PROD_LOGS_BUCKET", BUCKET)
    monkeypatch.setenv("PROD_PROMETHEUS_URL", "http://prometheus.test:9090")
    monkeypatch.setenv("DEV_LOGS_BUCKET", "shahdra-recall-logs-dev")
    monkeypatch.setenv("DEV_PROMETHEUS_URL", "http://dev-prometheus.test:9090")
    yield
