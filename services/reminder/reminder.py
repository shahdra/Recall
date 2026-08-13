"""Daily "you have cards due" digest, published to SNS.

Run once a day by a Kubernetes CronJob (infra/k8s/<env>/reminder/cronjob.yaml).
This is the committed Flow C of docs/spec.md:226 and the only consumer of
RECALL_SNS_TOPIC_ARN.

WHY ONE AGGREGATE DIGEST RATHER THAN A MESSAGE PER LEARNER: Recall has no
authentication. A learner is a random id minted in the browser and kept in
localStorage (services/frontend/components/app-shell.tsx:21-35), so
"learner-a3f9x2k1" has no email address and nothing to address a personal reminder
to. The digest therefore counts across everyone and goes to whoever subscribed to
the topic — in practice the developer running the demo.

WHY THIS DOES NOT IMPORT study-mcp's storage.py: services in this repo are strictly
self-contained. The tutor-agent reaches study-mcp over MCP and imports none of its
code, and study-mcp's own Dockerfile copies only `sm2.py storage.py app.py`. There
is no shared package to import from, and creating one for a single ~15-line query
would be the larger change. The DynamoDB access below therefore MIRRORS
storage.query_due_cards (services/study-mcp/storage.py:133) rather than calling it,
which is a real duplication: if that query's key schema changes, this must change
too. The GSI name and key names are the coupling.
"""

import logging
import os
import sys
from datetime import date

import boto3
from boto3.dynamodb.conditions import Key

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("reminder")

AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
CARDS_TABLE = os.environ.get("RECALL_CARDS_TABLE", "Cards")
PROFILE_TABLE = os.environ.get("RECALL_PROFILE_TABLE", "LearnerProfile")
SNS_TOPIC_ARN = os.environ.get("RECALL_SNS_TOPIC_ARN", "")


def _today_iso() -> str:
    """Today, as an ISO date string.

    NOTE: this deliberately does NOT honour the demo clock. study-mcp's offset
    (app.py:97) is in-process state in *that* container, so a separate CronJob pod
    cannot observe it. Advancing the demo clock therefore does not move the
    reminder's idea of today — which is the honest behaviour for a job that reports
    on real elapsed time, but worth knowing before demoing the two together.
    """
    return date.today().isoformat()


def list_learner_ids(ddb) -> list[str]:
    """Every learner id known to the system.

    A Scan, which is normally the wrong tool. It is right here for two reasons:
    there is no authentication, so the learner set is a handful of browser-minted
    ids rather than a user base; and this runs once a day, so even a full-table
    read costs almost nothing. LearnerProfile has one item per learner and no index
    to query instead.

    If Recall ever grows real accounts, this is the line that needs revisiting.
    """
    table = ddb.Table(PROFILE_TABLE)
    user_ids: list[str] = []

    # Paginate. A Scan returns at most 1MB per call, and taking only the first
    # page would silently under-count the moment the table outgrows it.
    kwargs: dict = {"ProjectionExpression": "user_id"}
    while True:
        response = table.scan(**kwargs)
        user_ids.extend(
            item["user_id"] for item in response.get("Items", []) if item.get("user_id")
        )
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return user_ids
        kwargs["ExclusiveStartKey"] = last_key


def count_due_cards(ddb, user_id: str, today_iso: str) -> int:
    """How many cards are due for one learner on or before ``today_iso``.

    Mirrors storage.query_due_cards (services/study-mcp/storage.py:133): same
    `due-index` GSI, same `user_id` partition + `due_date` range condition. The
    `.lte()` works because due_date is stored as an ISO STRING and those sort
    lexicographically in date order.

    Uses Select=COUNT so DynamoDB returns a number instead of the items — the
    digest needs a total, and fetching every card's front/back to discard them
    would be wasteful.
    """
    table = ddb.Table(CARDS_TABLE)
    total = 0

    kwargs: dict = {
        "IndexName": "due-index",
        "KeyConditionExpression": Key("user_id").eq(user_id)
        & Key("due_date").lte(today_iso),
        "Select": "COUNT",
    }
    while True:
        response = table.query(**kwargs)
        total += response.get("Count", 0)
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            return total
        kwargs["ExclusiveStartKey"] = last_key


def build_message(total_cards: int, learner_count: int, today_iso: str) -> str:
    """The digest text.

    Singular/plural is handled because "1 cards due" in a daily email is the kind
    of small wrongness that makes a project look unfinished.
    """
    card_word = "card" if total_cards == 1 else "cards"
    learner_word = "learner" if learner_count == 1 else "learners"
    return (
        f"Recall: {total_cards} {card_word} due today "
        f"across {learner_count} {learner_word} ({today_iso})."
    )


def main() -> int:
    if not SNS_TOPIC_ARN:
        # Misconfiguration, not "nothing to do" — exit non-zero so the CronJob is
        # recorded as failed and the missing ConfigMap value is noticed.
        logger.error("RECALL_SNS_TOPIC_ARN is not set; nothing to publish to")
        return 1

    today_iso = _today_iso()
    ddb = boto3.resource("dynamodb", region_name=AWS_REGION)

    learner_ids = list_learner_ids(ddb)
    # One Query per learner. Fine at this scale, and there is no way to ask the
    # GSI for "all learners' due cards" — user_id is its partition key, so a
    # cross-learner read would be a full index Scan.
    counts = {uid: count_due_cards(ddb, uid, today_iso) for uid in learner_ids}

    total_cards = sum(counts.values())
    learners_with_due = sum(1 for n in counts.values() if n > 0)

    if total_cards == 0:
        # Publishing "0 cards due" every morning trains the recipient to ignore
        # the topic, which costs the one message that matters. Exit 0: nothing due
        # is a successful run, not a failure.
        logger.info("nothing due on %s across %d learners; not publishing",
                    today_iso, len(learner_ids))
        return 0

    message = build_message(total_cards, learners_with_due, today_iso)
    boto3.client("sns", region_name=AWS_REGION).publish(
        TopicArn=SNS_TOPIC_ARN,
        Subject="Recall: cards due today",
        Message=message,
    )
    logger.info("published: %s", message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
