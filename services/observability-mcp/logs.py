"""Reading Recall's container logs back out of S3.

Fluent Bit runs as a DaemonSet on every node, tails the container runtime's log
files, enriches each record with Kubernetes metadata, and ships gzipped NDJSON to
the logs bucket. This module is the read side: given a time window and optionally
a service, find the objects that could hold matching records and return the lines.

Everything here is pure I/O plus parsing — no MCP, no tool decorators — so the
tests exercise the awkward parts (doubly-encoded records, nanosecond timestamps,
day-boundary spanning) without going through a transport.

Why S3 and not `kubectl logs`: a pod's logs die with the pod. The failures worth
investigating are usually the ones that killed the pod, so the logs have to
outlive it.
"""

import gzip
import json
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

KEY_PREFIX = "logs/"
"""Matches the `s3_key_format` in the Fluent Bit ConfigMap: logs/YYYY/MM/DD/…"""

OBJECT_TIME_MARGIN = timedelta(minutes=2)
"""Widen the LastModified pre-filter by this much on each side.

An object is uploaded when it fills or when `upload_timeout` expires, so it holds
records written *before* its own LastModified. Filtering objects exactly to the
requested window would silently drop the oldest records in each file.
"""

MAX_OBJECTS = 400
"""Ceiling on objects opened for one call.

A wide window on a busy day can match thousands. Reading them all would hang the
tool and blow up the response; the cap keeps a bad question cheap. Callers are
told when it trips, so a truncated answer never looks complete.
"""


def day_prefixes(start: datetime, end: datetime) -> list[str]:
    """Every `logs/YYYY/MM/DD/` prefix covering the window.

    Usually one, two when the window crosses midnight UTC. Iterating days rather
    than listing the whole bucket is what keeps a one-hour query from paging
    through months of history.
    """
    prefixes = []
    day = start.date()
    while day <= end.date():
        prefixes.append(f"{KEY_PREFIX}{day.year:04d}/{day.month:02d}/{day.day:02d}/")
        day += timedelta(days=1)
    return prefixes


def list_objects(s3, bucket: str, start: datetime, end: datetime) -> tuple[list[dict], bool]:
    """Log objects whose LastModified overlaps the window, oldest first.

    Returns the objects and whether MAX_OBJECTS truncated the list, so the caller
    can say so rather than implying it read everything.
    """
    lo, hi = start - OBJECT_TIME_MARGIN, end + OBJECT_TIME_MARGIN
    found: dict[str, dict] = {}
    paginator = s3.get_paginator("list_objects_v2")

    for prefix in day_prefixes(start, end):
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                modified = obj.get("LastModified")
                if modified is None:
                    continue
                if lo <= modified.astimezone(timezone.utc) <= hi:
                    found[obj["Key"]] = obj

    ordered = sorted(found.values(), key=lambda o: o["LastModified"])
    return ordered[:MAX_OBJECTS], len(ordered) > MAX_OBJECTS


def _decompress(body: bytes) -> str:
    """Text of one log object.

    Fluent Bit gzips, but tolerate a plain object too: a bucket written by hand
    during debugging should still be readable rather than raising.
    """
    try:
        return gzip.decompress(body).decode("utf-8", errors="replace")
    except (OSError, EOFError):
        return body.decode("utf-8", errors="replace")


def unwrap(record: dict) -> dict:
    """Flatten a record whose `log` field is itself JSON.

    The container runtime writes a JSON line; Fluent Bit's parser wraps that in
    another envelope. The real message and the Kubernetes metadata can therefore
    sit one level down:

        {"date": …, "log": "{\\"log\\": \\"…\\", \\"kubernetes\\": {…}}"}

    Merge the inner object up, keeping outer fields it lacks. A no-op for records
    that are already flat, so callers never have to know which shape they have.
    """
    inner_raw = record.get("log")
    if not isinstance(inner_raw, str):
        return record

    text = inner_raw.strip()
    if not (text.startswith("{") and text.endswith("}")):
        return record

    try:
        inner = json.loads(text)
    except json.JSONDecodeError:
        return record
    if not isinstance(inner, dict):
        return record

    merged = {**inner}
    for key in ("host", "kubernetes"):
        if key in record and key not in merged:
            merged[key] = record[key]
    if "date" in record:
        merged.setdefault("_outer_date", record["date"])
    return merged


def record_ts(record: dict) -> float | None:
    """Epoch seconds for a record, or None if no field carries a usable time.

    Three shapes have to work: Fluent Bit's numeric `date`, an ISO string, and the
    container runtime's nanosecond-precision `time`. That last one is why this
    isn't a bare `fromisoformat` call — it accepts at most six fractional digits
    and raises on nine.
    """
    for key in ("time", "date", "timestamp", "_outer_date"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if not isinstance(value, str) or not value:
            continue

        text = value
        if "." in text:
            head, frac = text.split(".", 1)
            frac = frac.rstrip("Z")
            offset = ""
            for sign in ("+", "-"):
                if sign in frac:
                    frac, rest = frac.split(sign, 1)
                    offset = sign + rest
                    break
            text = f"{head}.{frac[:6]}{offset or '+00:00'}"
        elif text.endswith("Z"):
            text = text[:-1] + "+00:00"

        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def iter_records(s3, bucket: str, start: datetime, end: datetime):
    """Yield parsed records from the objects overlapping the window.

    Time filtering is the caller's job: an object's records straddle the window
    edges, and which ones count depends on the question being asked.
    """
    objects, _ = list_objects(s3, bucket, start, end)
    for obj in objects:
        body = s3.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
        for line in _decompress(body).splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                yield unwrap(json.loads(line))
            except json.JSONDecodeError:
                # One malformed line should not lose the rest of the object.
                yield {"log": line}


def normalize_service(name: str) -> str:
    """Reduce a name to something comparable across the shapes it appears in.

    A pod is `tutor-agent-7d4f8b9c6-x2k9p` and its container is `tutor-agent`;
    both should match the service `tutor-agent`. Trimming the generated suffixes
    is what lets a human type the service name they know.
    """
    text = (name or "").strip().lower().replace("_", "-").lstrip("/")
    if text.endswith("-service"):
        text = text[: -len("-service")]

    # Strip a ReplicaSet hash + pod suffix (…-7d4f8b9c6-x2k9p) or a bare pod
    # ordinal, leaving the workload name.
    parts = text.split("-")
    while len(parts) > 1 and _looks_generated(parts[-1]):
        parts.pop()
    return "-".join(parts) or text


def _looks_generated(segment: str) -> bool:
    """Whether a name segment looks like a Kubernetes-generated suffix.

    Requires a digit so real words survive: `agent` stays, `7d4f8b9c6` goes.
    Length-bounded so a legitimate segment like `mcp` or `v2` is not eaten.
    """
    if not segment or len(segment) > 10:
        return False
    if not any(ch.isdigit() for ch in segment):
        return False
    return all(ch.isalnum() for ch in segment)


def record_identity(record: dict) -> dict:
    """Pull pod / container / namespace out of a record.

    Fluent Bit's `kubernetes` filter adds these; a record from a plain Docker
    setup will not have them, so every field is optional and callers fall back to
    matching the log text.
    """
    meta = record.get("kubernetes")
    if not isinstance(meta, dict):
        meta = {}

    labels = meta.get("labels")
    labels = labels if isinstance(labels, dict) else {}

    return {
        "pod": meta.get("pod_name"),
        "container": meta.get("container_name"),
        "namespace": meta.get("namespace_name"),
        # The app label is the most reliable service identity when it is present:
        # it survives a pod restart and a ReplicaSet roll, which the pod name does not.
        "app": labels.get("app") or labels.get("app.kubernetes.io/name"),
        "host": record.get("host"),
    }


def matches_service(record: dict, wanted: str) -> bool:
    """Whether a record belongs to the named service.

    Prefers the Kubernetes labels, then the container and pod names, and falls
    back to a substring match on the log text so a record with no metadata is
    never silently dropped from a search.
    """
    identity = record_identity(record)
    for field in ("app", "container", "pod"):
        value = identity.get(field)
        if value and normalize_service(value) == wanted:
            return True

    if any(identity.get(f) for f in ("app", "container", "pod")):
        return False

    return wanted in str(record.get("log", "")).lower()
