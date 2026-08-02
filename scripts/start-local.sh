#!/bin/bash
# Start both Recall services against DynamoDB Local for end-to-end checks.
ROOT=/Users/saed/shahd/Recall
# Real credentials: Bedrock needs them, and DynamoDB Local ignores auth anyway.
# Dummy keys here would break every model call.
export AWS_REGION=us-east-1
# Scoped to DynamoDB only. The unscoped AWS_ENDPOINT_URL would also redirect
# Bedrock to DynamoDB Local, breaking every model call.
export AWS_ENDPOINT_URL_DYNAMODB=http://localhost:8001

lsof -ti:8010 2>/dev/null | xargs kill -9 2>/dev/null
lsof -ti:9001 2>/dev/null | xargs kill -9 2>/dev/null
sleep 2

cd "$ROOT/services/study-mcp" || exit 1
PORT=9001 nohup .venv/bin/python app.py > /tmp/e2e_mcp.log 2>&1 &
sleep 6
echo "study-mcp: $(curl -s http://127.0.0.1:9001/health)"

cd "$ROOT/services/tutor-agent" || exit 1
set -a; . ./.env; set +a
STUDY_MCP_URL=http://127.0.0.1:9001/mcp PORT=8010 \
  nohup .venv/bin/python app.py > /tmp/e2e_agent.log 2>&1 &
sleep 15
echo "tutor-agent: $(curl -s http://127.0.0.1:8010/health)"
grep -E "discovered [0-9]+ study-mcp" /tmp/e2e_agent.log | tail -1
