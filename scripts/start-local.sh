#!/bin/bash
# Start study-mcp and tutor-agent locally against DynamoDB Local.
#
#   docker run -d --rm --name recall-ddb -p 8001:8000 amazon/dynamodb-local
#   ./scripts/setup-local-dynamodb.sh
#   ./scripts/start-local.sh
#
# Bedrock is reached with your real AWS profile; only DynamoDB is redirected.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export AWS_REGION="${AWS_REGION:-us-east-1}"

# Only study-mcp talks to DynamoDB; the tutor-agent reaches it through MCP and
# uses AWS only for Bedrock and S3. So the endpoint override goes on study-mcp
# alone, and it is DynamoDB-scoped: the unscoped AWS_ENDPOINT_URL applies to
# every service in boto3 and would send Bedrock traffic to the local database.
DDB_ENDPOINT="${DDB_ENDPOINT:-http://localhost:8001}"

# DynamoDB Local partitions its data by access key, so study-mcp must use the
# same key setup-local-dynamodb.sh used or it gets a different, empty database.
#
# These are deliberately NOT exported globally: boto3 credentials are global even
# though endpoints can be scoped per service, so exporting a dummy key here would
# make the tutor-agent authenticate to Bedrock with fake credentials and every
# model call would fail. study-mcp (DynamoDB only) gets the dummy key; the
# tutor-agent (Bedrock) keeps your real profile.
DDB_ACCESS_KEY="${DDB_ACCESS_KEY:-local}"
DDB_SECRET_KEY="${DDB_SECRET_KEY:-local}"

MCP_PORT="${MCP_PORT:-9001}"
AGENT_PORT="${AGENT_PORT:-8010}"

# Fail early rather than serving a stack that cannot read or write anything.
if ! AWS_ACCESS_KEY_ID="$DDB_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$DDB_SECRET_KEY" \
     aws dynamodb describe-table --endpoint-url "$DDB_ENDPOINT" \
        --table-name Cards >/dev/null 2>&1; then
  echo "ERROR: the Cards table is missing at $DDB_ENDPOINT" >&2
  echo "       Run ./scripts/setup-local-dynamodb.sh first." >&2
  echo "       (If you just ran it, check that AWS_ACCESS_KEY_ID matches:" >&2
  echo "        DynamoDB Local keeps a separate database per access key.)" >&2
  exit 1
fi

lsof -ti:"$AGENT_PORT" 2>/dev/null | xargs kill -9 2>/dev/null
lsof -ti:"$MCP_PORT" 2>/dev/null | xargs kill -9 2>/dev/null
sleep 2

cd "$ROOT/services/study-mcp" || exit 1
AWS_ACCESS_KEY_ID="$DDB_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$DDB_SECRET_KEY" \
  AWS_ENDPOINT_URL_DYNAMODB="$DDB_ENDPOINT" PORT="$MCP_PORT" nohup .venv/bin/python app.py > /tmp/recall_mcp.log 2>&1 &
sleep 6
echo "study-mcp:   $(curl -s "http://127.0.0.1:$MCP_PORT/health")"

cd "$ROOT/services/tutor-agent" || exit 1
# Local secrets (DEEPGRAM_API_KEY). Gitignored; absent in CI, where voice is off.
if [ -f ./.env ]; then
  set -a; . ./.env; set +a
fi
STUDY_MCP_URL="http://127.0.0.1:$MCP_PORT/mcp" PORT="$AGENT_PORT" \
  nohup .venv/bin/python app.py > /tmp/recall_agent.log 2>&1 &
sleep 15
echo "tutor-agent: $(curl -s "http://127.0.0.1:$AGENT_PORT/health")"
grep -E "discovered [0-9]+ study-mcp" /tmp/recall_agent.log | tail -1

echo
echo "Logs: /tmp/recall_mcp.log  /tmp/recall_agent.log"
