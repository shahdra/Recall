#!/usr/bin/env python3
"""End-to-end learner journey against live services.

Not a pytest module: this drives the running system over HTTP, so it needs real
Bedrock credentials, a live study-mcp, and DynamoDB Local. Run it by hand when
changing the API surface or the agent wiring.

    docker run -d --rm --name recall-ddb -p 8001:8000 amazon/dynamodb-local
    ./scripts/setup-local-dynamodb.sh
    ./scripts/start-local.sh
    python services/tutor-agent/tests/integration/e2e_journey.py

Exercises every layer at once: real Nova generating cards, MCP persistence to
DynamoDB, the Grader, deterministic SM-2 rescheduling, and the orchestrator
choosing a tool unprompted.
"""

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
USER = "e2e-user"
TIMEOUT_SECONDS = 180


def post(path, payload):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        sys.exit(f"POST {path} failed with HTTP {exc.code}: {exc.read().decode()}")
    except urllib.error.URLError as exc:
        sys.exit(
            f"Could not reach {BASE}{path} ({exc.reason}). Is start-local.sh running?"
        )


def main():
    print("=== 1. Create a deck from pasted material (real Nova generates cards) ===")
    deck = post(
        "/decks",
        {
            "user_id": USER,
            "title": "Cell Biology",
            "text": (
                "Mitochondria are the powerhouse of the cell, producing ATP through "
                "cellular respiration. Chloroplasts conduct photosynthesis, "
                "converting light energy into glucose. Ribosomes synthesize proteins "
                "from mRNA. The nucleus stores the cell's DNA."
            ),
        },
    )
    print(f"  deck_id={deck['deck_id']}  card_count={deck['card_count']}")
    assert deck["card_count"] > 0, "no cards generated"

    print("\n=== 2. Start a session (profile + due cards from DynamoDB) ===")
    session = post("/session/start", {"user_id": USER})
    cards = session["cards"]
    print(f"  cards due: {len(cards)}")
    for card in cards[:3]:
        print(f"    - {card['front'][:65]}")
    assert cards, "no due cards"

    card = cards[0]
    print("\n=== 3. Answer CORRECTLY (real Grader + real SM-2) ===")
    print(f"  Q: {card['front']}")
    correct = post(
        "/session/answer",
        {
            "user_id": USER,
            "deck_id": card["deck_id"],
            "card_id": card["card_id"],
            "card_front": card["front"],
            "card_back": card["back"],
            "student_answer": card["back"],
        },
    )
    print(
        f"  -> is_correct={correct['is_correct']} quality={correct['quality']} "
        f"interval={correct['interval_days']}d due={correct['due_date']}"
    )
    assert correct["is_correct"] is True, "a correct answer was graded wrong"

    print("\n=== 4. Answer WRONGLY (must resurface tomorrow) ===")
    wrong = post(
        "/session/answer",
        {
            "user_id": USER,
            "deck_id": card["deck_id"],
            "card_id": card["card_id"],
            "card_front": card["front"],
            "card_back": card["back"],
            "student_answer": "purple bananas from the moon",
        },
    )
    print(
        f"  -> is_correct={wrong['is_correct']} quality={wrong['quality']} "
        f"interval={wrong['interval_days']}d due={wrong['due_date']}"
    )
    assert wrong["is_correct"] is False, "a wrong answer was graded correct"
    assert wrong["interval_days"] == 1, "a lapsed card did not reset to a 1-day interval"

    print("\n=== 5. Orchestrator reports progress, choosing its own tool ===")
    chat = post(
        "/chat", {"user_id": USER, "message": f"How am I doing? My user_id is {USER}."}
    )
    print(f"  tools_called: {chat['tools_called']}")
    print(f"  iterations={chat['iterations']} capped={chat['capped']}")
    print(f"  response: {chat['response'][:180]}")
    assert chat["tools_called"], "the orchestrator answered without consulting a tool"

    print("\nALL STEPS PASSED")


if __name__ == "__main__":
    main()
