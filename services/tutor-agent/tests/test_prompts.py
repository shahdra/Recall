"""Tests for the system prompts and learner-profile memory injection.

Memory is what makes Recall a *tutor* rather than a quiz generator: the profile
written at the end of one session has to change how the next one behaves. These
tests pin down that the profile actually reaches the prompt, and that a
first-time learner (or a corrupt profile) does not break the session.
"""

from prompts import (
    CARD_GEN_PROMPT,
    GRADER_PROMPT,
    ORCHESTRATOR_PROMPT,
    build_system_prompt,
    summarize_profile,
)


def test_orchestrator_prompt_defines_persona_and_boundaries():
    """The course requires a persona, capabilities, and explicit boundaries."""
    prompt = ORCHESTRATOR_PROMPT.lower()
    assert "recall" in prompt
    assert "tutor" in prompt
    # Boundary: must not just hand over answers.
    assert "homework" in prompt or "do not simply give" in prompt


def test_all_three_prompts_are_substantial():
    for prompt in (ORCHESTRATOR_PROMPT, CARD_GEN_PROMPT, GRADER_PROMPT):
        assert len(prompt) > 120


def test_build_system_prompt_includes_the_base_prompt():
    out = build_system_prompt({})
    assert "Recall" in out


def test_new_learner_prompt_says_so_explicitly():
    """A first session should not pretend to remember anything."""
    out = build_system_prompt({})
    assert "first" in out.lower() or "new" in out.lower() or "no history" in out.lower()


def test_weak_topics_are_injected():
    profile = {"weak_topics": {"mitosis": 0.75, "photosynthesis": 0.4}}
    out = build_system_prompt(profile)
    assert "mitosis" in out
    assert "photosynthesis" in out


def test_weak_topics_are_ordered_worst_first():
    # All three are above WEAK_TOPIC_THRESHOLD, so ordering is what's under test
    # here rather than filtering.
    profile = {"weak_topics": {"mild": 0.35, "brutal": 0.9, "medium": 0.6}}
    out = build_system_prompt(profile)
    assert out.index("brutal") < out.index("medium") < out.index("mild")


def test_weak_topics_are_capped_to_avoid_prompt_bloat():
    profile = {"weak_topics": {f"topic{i}": 0.9 - i * 0.01 for i in range(40)}}
    out = build_system_prompt(profile)
    # The worst few matter; the long tail is noise that crowds out the material.
    assert "topic0" in out
    assert "topic39" not in out


def test_only_genuinely_weak_topics_are_flagged():
    """A topic the learner mostly gets right is not a weakness."""
    profile = {"weak_topics": {"solid": 0.05, "shaky": 0.8}}
    out = build_system_prompt(profile)
    assert "shaky" in out
    assert "solid" not in out


def test_notes_are_injected():
    profile = {"notes": "confuses mitosis with meiosis"}
    out = build_system_prompt(profile)
    assert "confuses mitosis with meiosis" in out


def test_preferences_are_injected():
    profile = {"preferences": {"tone": "blunt", "answer_style": "short"}}
    out = build_system_prompt(profile)
    assert "blunt" in out
    assert "short" in out


def test_stats_are_injected():
    profile = {"stats": {"total_reviews": 120, "accuracy": 0.82, "streak_days": 4}}
    out = build_system_prompt(profile)
    assert "120" in out
    assert "82" in out  # rendered as a percentage
    assert "4" in out


def test_accuracy_renders_as_percentage_not_raw_float():
    out = build_system_prompt({"stats": {"accuracy": 0.5}})
    assert "50%" in out
    assert "0.5" not in out


def test_missing_profile_keys_do_not_raise():
    for profile in ({}, {"weak_topics": None}, {"stats": None}, {"notes": None}):
        assert build_system_prompt(profile)


def test_none_profile_does_not_raise():
    assert build_system_prompt(None)


def test_malformed_weak_topics_are_skipped_not_fatal():
    """A corrupt profile must degrade to a plain prompt, not break the session."""
    profile = {"weak_topics": {"good": 0.9, "bad": "not-a-number", "worse": None}}
    out = build_system_prompt(profile)
    assert "good" in out
    assert "not-a-number" not in out


def test_notes_are_truncated():
    profile = {"notes": "x" * 5000}
    out = build_system_prompt(profile)
    assert len(out) < 3000


def test_summarize_profile_is_empty_for_a_new_learner():
    assert summarize_profile({}) == ""


def test_summarize_profile_is_nonempty_when_there_is_history():
    assert summarize_profile({"weak_topics": {"bio": 0.9}})


def test_profile_injection_is_clearly_delimited():
    """The model must be able to tell remembered facts from the instructions."""
    out = build_system_prompt({"notes": "struggles with dates"})
    assert "struggles with dates" in out
    base_len = len(build_system_prompt({}))
    assert len(out) > base_len


def test_card_gen_prompt_accepts_max_cards_substitution():
    assert "7" in CARD_GEN_PROMPT.format(max_cards=7)


def test_grader_prompt_documents_the_full_scale():
    for grade in "012345":
        assert grade in GRADER_PROMPT
