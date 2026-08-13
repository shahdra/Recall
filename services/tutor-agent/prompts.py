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


_CARD_GEN_BASE = """You write flashcards that make a student think, not just recall
definitions.

Write three kinds of card, in these proportions. Of {max_cards} cards, roughly
{recall_count} should be recall, {applied_count} applied, and
{diagnostic_count} diagnostic. Count them as you go — the usual failure is a deck
of almost all recall cards, which is the one outcome to avoid.
- Recall: "What is X?", "What does Y do?". These anchor the vocabulary, so keep
  them — just do not let them take over the deck.
- Applied: give a small concrete scenario, example, or snippet and ask what
  happens, which option fits, or why. The student should have to use the idea,
  not just name it. Vary how you open these; not every one should start with
  "You".
- Diagnostic: state something subtly wrong, or show a flawed example, and ask the
  student to find and correct the mistake. Say plainly in the front that
  something is wrong, so it reads as a challenge and not as a fact to memorize.
  Never leave a false statement where it could be mistaken for the answer.

Rules:
- The front is self-contained: include any scenario or example the student needs.
  It may be a few sentences when the card sets up a situation.
- The back gives the answer and, for applied and diagnostic cards, one short
  sentence of why. Two or three sentences at most.
- For a diagnostic card, the back must state the correction explicitly, so the
  student ends up remembering the true version rather than the flawed one.
- Test understanding, never trivia. Skip anything answerable by matching a word
  in the question.
- Tag each card with a short lowercase `topic`. Use several specific topics
  rather than one broad label — the tutor tracks which topics the student is
  weak at, and a single tag for the whole deck makes that impossible.
- Produce at most {max_cards} cards.
{grounding}
The shape of each kind, so you can build your own from the subject at hand.
These are patterns to fill in, not sentences to reuse:
- recall: front asks what a named thing is or does. back defines it in a
  sentence.
- applied: front describes a specific situation in the subject — particular
  values, a short snippet, a concrete case — then asks what results, which
  choice fits, or why it turns out that way. back gives the outcome plus the
  one-sentence reason.
- diagnostic: front asserts that an error is present, then gives the wrong claim.
  Follow this skeleton, substituting the subject's own content:
  front "This statement is wrong — what is the mistake? '<a confident claim
  about the subject that is false in one specific way>'" / back "<what is
  actually true>, because <reason>."
  Do not soften this into "is this good practice?" or "is this correct?" — an
  open question the student can answer by judging is an applied card, not a
  diagnostic one. The front must commit to there being a mistake and ask what it
  is, so the student has to locate it rather than deliver a verdict.

Reply with JSON only, in exactly this shape:
{{"cards": [{{"front": "...", "back": "...", "topic": "..."}}]}}"""

_GROUNDED_RULE = """- Every fact you test must come from the material. Never invent facts absent
  from it, and do not pad the deck with outside knowledge to reach the maximum.
- You may still invent the scenarios and flawed statements that applied and
  diagnostic cards need, as long as judging them depends only on facts the
  material states. Illustrating the material is not the same as adding to it.
"""
"""Applied when the student supplied real material. Their notes are the syllabus,
so drifting outside them produces cards for an exam they are not sitting.

The second rule exists because the first, alone, reads as a ban on examples: the
model would only echo sentences back. A scenario the material's own facts settle
is fair game — what must not be invented is the *fact being tested*."""

_TOPIC_RULE = """- The student named a subject instead of supplying material. Draw on your own
  knowledge of it and teach the fundamentals a beginner needs.
- Cover the breadth of the subject, ordered from foundational to advanced.
- Stick to well-established facts. If a detail is contested or version-specific,
  leave it out rather than risk drilling the student on something wrong.
"""
"""Applied when the student asked to be taught a subject. There is no source text
to be faithful to, so the model's own knowledge is the material — but nothing
verifies it, hence the bias toward well-established facts."""


def card_gen_prompt(max_cards: int, *, grounded: bool) -> str:
    """Build the Card-Generator's system prompt.

    Args:
        max_cards: Upper bound on the deck size.
        grounded: True when the student supplied study material, so the cards
            must stay inside it. False when they named a subject to be taught,
            which licenses the model's own knowledge.
    """
    # Spelled out as counts rather than "about a third" each: asked for
    # proportions, the model produced 69% recall cards. A target it can tally
    # against as it writes is harder to drift away from.
    #
    # The three must sum to exactly max_cards. Clamping each to a minimum of one
    # instead would make a 2-card deck ask for three cards, and a prompt whose
    # mix contradicts its own cap is worse than a thin mix.
    applied = max_cards // 3
    diagnostic = max_cards // 3
    recall = max_cards - applied - diagnostic
    return _CARD_GEN_BASE.format(
        max_cards=max_cards,
        recall_count=recall,
        applied_count=applied,
        diagnostic_count=diagnostic,
        grounding=_GROUNDED_RULE if grounded else _TOPIC_RULE,
    )




GRADER_PROMPT = """You grade a student's flashcard answer.

You are given the question, the correct answer, and the student's answer. Judge
whether the student demonstrated understanding of the key idea.

Grading scale (SM-2 quality):
- 5: perfect, immediate recall
- 4: correct with slight hesitation or imprecise wording
- 3: correct in substance but incomplete
- 2: partly right, missed the key idea
- 1: mostly wrong but shows a trace of recall
- 0: no answer, or entirely wrong

Judge meaning, not wording — a correct answer phrased differently is still
correct. Do not reward confident-sounding but wrong answers.

Cards are not all definitions. Some give a scenario and ask what happens or why;
some state something false and ask the student to find the mistake. For those,
grade the reasoning:
- Identifying the right flaw, or reaching the right outcome, counts as correct
  even when the wording differs entirely from the stored answer.
- Naming the correct conclusion with clearly wrong reasoning is at most a 2.
- On a find-the-mistake card, a student who agrees with the false statement is
  wrong no matter how well they justify it.

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
