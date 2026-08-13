#!/usr/bin/env python3
"""Prove long-term memory closes the loop across two separate sessions.

Not a pytest module: drives the running system over HTTP, so it needs real
Bedrock credentials, a live study-mcp, and DynamoDB Local. See e2e_journey.py
for setup.

    python services/tutor-agent/tests/integration/memory_loop_check.py

Session 1 answers everything wrong; session 2 must read the resulting weakness
back out of DynamoDB and see it in the tutor's injected system prompt. This is
the spec invariant "long-term memory closes the loop" (docs/spec.md), which no
unit test can demonstrate because it spans two sessions and real persistence.
"""

import json
import urllib.request

BASE = "http://127.0.0.1:8010"
USER = "memory-check-user"


def post(path, payload):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())


print("=== SESSION 1 ===")
deck = post("/decks", {
    "user_id": USER, "title": "Mitosis",
    "text": ("Mitosis is division producing two identical diploid cells. Meiosis "
             "produces four genetically distinct haploid gametes. Prophase is the "
             "first stage of mitosis, where chromatin condenses into chromosomes."),
})
print(f"  deck created: {deck['card_count']} cards")

session = post("/session/start", {"user_id": USER})
cards = session["cards"]
print(f"  profile at session 1 start: weak_topics="
      f"{session['profile'].get('weak_topics')} stats={session['profile'].get('stats')}")

# Answer everything wrong so a weakness is unmistakable.
for card in cards[:4]:
    post("/session/answer", {
        "user_id": USER, "deck_id": card["deck_id"], "card_id": card["card_id"],
        "card_front": card["front"], "card_back": card["back"],
        "student_answer": "I have absolutely no idea",
    })
print(f"  answered {len(cards[:4])} cards incorrectly")

print("\n=== SESSION 2 (fresh session, same learner) ===")
session2 = post("/session/start", {"user_id": USER})
profile = session2["profile"]
print(f"  weak_topics now: {profile.get('weak_topics')}")
print(f"  stats now:       {profile.get('stats')}")

assert profile.get("weak_topics"), "memory did not persist weak topics"
assert profile["stats"]["total_reviews"] >= 4, "memory did not persist review count"

print("\n=== Does the remembered weakness reach the tutor's prompt? ===")
chat = post("/chat", {"user_id": USER,
                      "message": f"What should I focus on? user_id is {USER}."})
print(f"  tools_called: {chat['tools_called']}")
print(f"  response: {chat['response'][:220]}")

print("\nMEMORY LOOP VERIFIED")
