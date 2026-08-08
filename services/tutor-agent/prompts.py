"""System prompts, and the learner profile injected into them.

All three agents' prompts live here so the tutor's voice and boundaries can be
read in one place rather than reconstructed from three modules.

``build_system_prompt`` is what makes Recall a tutor rather than a quiz
generator: it folds the learner's stored profile — weak topics, stated
preferences, running stats, free-text notes — into the orchestrator's prompt, so
the profile written at the end of one session changes how the next one behaves.

Every function here degrades rather than raises. A corrupt or half-written
profile should cost the learner some personalization, never the session.
"""

import logging

logger = logging.getLogger(__name__)

MAX_WEAK_TOPICS = 5
"""Only the worst few. A long tail of near-zero miss rates crowds the real
material out of the context window without telling the tutor anything."""

WEAK_TOPIC_THRESHOLD = 0.3
"""Miss rate above which a topic counts as a weakness. Below this the learner is
mostly getting it right, and nagging about it would be noise."""

MAX_NOTES_CHARS = 600
"""Notes are free text written by the agent itself, so they can grow without
bound. Cap them before they crowd out the conversation."""


ORCHESTRATOR_PROMPT = """You are Recall, a patient and encouraging study tutor.

You help students remember what they study, using active recall and spaced
repetition. You quiz them on their own uploaded material, judge their answers,
and explain why an answer was right or wrong.

How you behave:
- Encouraging but honest. Never tell a student they were right when they were not.
- Explain the *why*, briefly. One or two sentences, not a lecture.
- Plain text only. No markdown, no bullet lists, no headings.
- Ask one question at a time and wait for the answer.

Your boundaries:
- You do not do a student's homework or assignments for them. If asked, offer to
  quiz them on the underlying material instead.
- You never invent facts. Everything you quiz comes from the student's material.
- You never guess at a student's data. To know what is due, how they are doing,
  or what they struggle with, call a tool and read the result.

You have tools for looking up due cards, generating cards from material, grading
answers, and checking progress. Use them rather than reasoning from memory."""


CARD_GEN_PROMPT = """You write flashcards for a student.

Given study material, produce clear question/answer flashcards. Rules:
- One fact per card. Keep the front a single question.
- The back is the answer, not an essay. One or two sentences.
- Tag each card with a short lowercase `topic` drawn from the material.
- Never invent facts absent from the material.
- Produce at most {max_cards} cards.

Reply with JSON only, in exactly this shape:
{{"cards": [{{"front": "...", "back": "...", "topic": "..."}}]}}"""


GRADER_PROMPT = """You grade a student's flashcard answer.

You are given the question, the correct answer, and the student's answer. Judge
whether the student demonstrated recall of the key idea.

Grading scale (SM-2 quality):
- 5: perfect, immediate recall
- 4: correct with slight hesitation or imprecise wording
- 3: correct in substance but incomplete
- 2: partly right, missed the key idea
- 1: mostly wrong but shows a trace of recall
- 0: no answer, or entirely wrong

Judge meaning, not wording — a correct answer phrased differently is still
correct. Do not reward confident-sounding but wrong answers.

Explain in one or two sentences, addressed to the student, saying *why*.

Reply with JSON only:
{"is_correct": true, "explanation": "...", "quality": 4}"""


_NEW_LEARNER_NOTE = """
--- What you remember about this student ---
This is a new student. You have no history with them yet, so do not imply that
you remember anything. Learn what they struggle with as you go."""


def _weak_topic_lines(weak_topics) -> list[str]:
    """Render the worst topics, worst first. Malformed entries are skipped."""
    if not isinstance(weak_topics, dict):
        return []

    scored = []
    for topic, miss_rate in weak_topics.items():
        try:
            rate = float(miss_rate)
        except (TypeError, ValueError):
            logger.warning("skipping malformed weak_topic %r=%r", topic, miss_rate)
            continue
        if rate >= WEAK_TOPIC_THRESHOLD:
            scored.append((rate, str(topic)))

    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [
        f"- {topic} (misses about {round(rate * 100)}% of the time)"
        for rate, topic in scored[:MAX_WEAK_TOPICS]
    ]


def _stats_line(stats) -> str:
    if not isinstance(stats, dict) or not stats:
        return ""

    parts = []
    reviews = stats.get("total_reviews")
    if reviews:
        parts.append(f"{int(reviews)} cards reviewed so far")

    accuracy = stats.get("accuracy")
    if accuracy is not None:
        try:
            parts.append(f"{round(float(accuracy) * 100)}% accuracy overall")
        except (TypeError, ValueError):
            pass

    streak = stats.get("streak_days")
    if streak:
        parts.append(f"a {int(streak)}-day streak")

    return "Their record: " + ", ".join(parts) + "." if parts else ""


def _preferences_line(preferences) -> str:
    if not isinstance(preferences, dict) or not preferences:
        return ""
    stated = ", ".join(f"{key}: {value}" for key, value in sorted(preferences.items()))
    return f"Preferences they've stated: {stated}."


def summarize_profile(profile) -> str:
    """Render a learner profile as prompt text, or "" if there is no history.

    Returning empty for a new learner lets the caller substitute an explicit
    "this is a new student" note instead of an awkwardly blank memory section.
    """
    if not isinstance(profile, dict) or not profile:
        return ""

    sections = []

    weak_lines = _weak_topic_lines(profile.get("weak_topics"))
    if weak_lines:
        sections.append("Topics they keep missing, worst first:\n" + "\n".join(weak_lines))

    stats = _stats_line(profile.get("stats"))
    if stats:
        sections.append(stats)

    preferences = _preferences_line(profile.get("preferences"))
    if preferences:
        sections.append(preferences)

    notes = profile.get("notes")
    if isinstance(notes, str) and notes.strip():
        trimmed = notes.strip()[:MAX_NOTES_CHARS]
        sections.append(f"Your own notes on this student: {trimmed}")

    return "\n\n".join(sections)


def build_system_prompt(profile) -> str:
    """Build the orchestrator's system prompt for one learner.

    Args:
        profile: The learner profile from study-mcp, or None/empty for a new one.

    Returns:
        The base prompt plus a clearly delimited memory section, so the model can
        tell remembered facts about the student from its standing instructions.
    """
    try:
        summary = summarize_profile(profile)
    except Exception:
        # Personalization is a nice-to-have; the session is not.
        logger.exception("could not summarize learner profile; using base prompt")
        summary = ""

    if not summary:
        return ORCHESTRATOR_PROMPT + _NEW_LEARNER_NOTE

    return (
        ORCHESTRATOR_PROMPT
        + "\n\n--- What you remember about this student ---\n"
        + summary
        + "\n\nUse this to decide what to drill and how to pitch your "
        "explanations. Do not read it back to them as a report."
    )
