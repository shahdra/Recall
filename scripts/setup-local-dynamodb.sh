#!/bin/bash
# Create Recall's three tables in DynamoDB Local.
#
# Start the database first:
#   docker run -d --rm --name recall-ddb -p 8001:8000 amazon/dynamodb-local
#
# Key schemas here must match infra/terraform/dynamodb.tf. If they drift, local
# runs pass while production breaks.
set -euo pipefail

ENDPOINT="${DDB_ENDPOINT:-http://localhost:8001}"
export AWS_REGION="${AWS_REGION:-us-east-1}"

# DynamoDB Local ignores credentials but boto3/CLI still require them to be set.
export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-local}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-local}"

create() {
  local name=$1; shift
  if aws dynamodb describe-table --endpoint-url "$ENDPOINT" --table-name "$name" \
      >/dev/null 2>&1; then
    echo "  $name already exists"
    return
  fi
  aws dynamodb create-table --endpoint-url "$ENDPOINT" --table-name "$name" \
      --billing-mode PAY_PER_REQUEST "$@" >/dev/null
  echo "  $name created"
}

echo "Creating Recall tables at $ENDPOINT"

create Cards \
  --attribute-definitions \
      AttributeName=deck_id,AttributeType=S \
      AttributeName=card_id,AttributeType=S \
      AttributeName=user_id,AttributeType=S \
      AttributeName=due_date,AttributeType=S \
  --key-schema \
      AttributeName=deck_id,KeyType=HASH \
      AttributeName=card_id,KeyType=RANGE \
  --global-secondary-indexes \
      'IndexName=due-index,KeySchema=[{AttributeName=user_id,KeyType=HASH},{AttributeName=due_date,KeyType=RANGE}],Projection={ProjectionType=ALL}'

create Decks \
  --attribute-definitions \
      AttributeName=user_id,AttributeType=S \
      AttributeName=deck_id,AttributeType=S \
  --key-schema \
      AttributeName=user_id,KeyType=HASH \
      AttributeName=deck_id,KeyType=RANGE

create LearnerProfile \
  --attribute-definitions AttributeName=user_id,AttributeType=S \
  --key-schema AttributeName=user_id,KeyType=HASH

echo "Done."
