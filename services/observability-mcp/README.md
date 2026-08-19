# observability-mcp

Recall's **second MCP server**: log and metric tools for whoever is debugging the
running system. Seven tools over **stdio**, so an editor or CLI agent launches it
directly.

## Why this is a separate server from study-mcp

Recall has two MCP servers on purpose, and the difference is who calls them.

| | `study-mcp` | `observability-mcp` |
|---|---|---|
| Caller | the tutor-agent, on every request | a human debugging an incident |
| Transport | HTTP, in-cluster | **stdio**, on a laptop |
| Deployed | dev + prod namespaces | not deployed — it is a client |
| Credentials | node role via IMDS | whoever launched it |

Log search is an operator tool: it reads production data in bulk, it is slow
enough to be interactive rather than in-request, and no tutoring turn needs it.
Deploying it beside the app would put a "read all production logs" surface inside
the request path for no benefit — so it runs where the person is instead.

## Tools

| Tool | Answers |
|---|---|
| `query_logs(env, service?, since_minutes, limit)` | "what has tutor-agent logged in the last 15 minutes?" |
| `query_logs_at(timestamp, env, service?, window_minutes, limit)` | "what happened around 14:02, when the alert fired?" |
| `log_activity(env, since_minutes)` | "is anything shipping logs at all?" — separates *no errors* from *no pipeline* |
| `query_metrics(env, promql)` | instant PromQL |
| `query_metrics_range(env, promql, since_minutes, step_seconds)` | the shape over time |
| `list_metrics(env, prefix)` | metric names — `prefix="recall_"` for just ours |
| `check_alerts(env, firing_only)` | which rules are pending or firing right now |

`env` is `dev` or `prod`. Every tool returns a JSON envelope and **never raises**:
an exception would reach an editor as a broken tool rather than an answer, so
failures come back as `{"ok": false, "error": …}`.

## The incident loop these are shaped around

The tools compose into the sequence you actually follow at 2am:

1. `check_alerts(env="prod")` — names the rule, e.g. `RecallLLMFailures`, and when it started
2. `query_logs_at(timestamp=<activeAt>, service="tutor-agent")` — the lines around that moment
3. `query_metrics_range(promql="rate(recall_llm_fallbacks_total[5m])")` — whether the fallback absorbed it

Each step's output is the next step's input, which is why `check_alerts` returns
`since` and why the log tools take a timestamp rather than only a lookback.

## Where the logs come from

Fluent Bit runs as a DaemonSet (`infra/k8s/monitoring/fluent-bit.yaml`), tails
each node's container logs, enriches every record with pod/namespace/labels via
the `kubernetes` filter, and ships gzipped NDJSON to the logs bucket under
`logs/YYYY/MM/DD/`.

**Why ship at all, when `kubectl logs` exists:** a pod's logs die with the pod,
and the failures worth investigating are disproportionately the ones that killed
it — an OOM, a crashloop, a node rotated out by the ASG. By the time anyone looks,
kubectl has nothing left to show.

The `kubernetes` filter is what lets you ask for `tutor-agent` instead of a
container hash, and what makes a query scopeable to one namespace so dev and prod
stay separable in a single bucket.

## Setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Then point it at an environment. The bucket name embeds the account id, so take it
from Terraform:

```bash
export AWS_REGION=us-east-1
export PROD_LOGS_BUCKET=$(cd ../../infra/terraform && terraform output -raw logs_bucket)
export PROD_PROMETHEUS_URL=https://prometheus.recall.fursa.click
```

AWS credentials come from the normal boto3 chain — your own profile or env vars.
Nothing in the cluster is granted read access to the log archive.

### As an MCP server

Register it with any MCP client, e.g.:

```json
{
  "mcpServers": {
    "recall-observability": {
      "command": "services/observability-mcp/.venv/bin/python",
      "args": ["services/observability-mcp/app.py"],
      "env": {
        "AWS_REGION": "us-east-1",
        "PROD_LOGS_BUCKET": "shahdra-recall-logs-<account-id>",
        "PROD_PROMETHEUS_URL": "https://prometheus.recall.fursa.click"
      }
    }
  }
}
```

## Tests

```bash
pytest -q                                  # 66 tests
pytest -q --cov=. --cov-report=term-missing
```

66 unit tests, **99%** on `app.py` and **94%** on `logs.py`. S3 is faked with
`moto` and Prometheus with a stub transport, so the suite needs no credentials and
no cluster — the same rule the rest of the repo follows: fake what costs money or
is non-deterministic.

The awkward parts are what the tests are for: records wrapped twice by the
runtime and the shipper, the container runtime's nanosecond timestamps that
`datetime.fromisoformat` cannot parse, windows that cross midnight UTC, and pod
names with generated suffixes.
