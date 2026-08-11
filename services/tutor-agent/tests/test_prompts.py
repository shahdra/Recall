"""Tests for the system prompts and learner-profile memory injection.

Memory is what makes Recall a *tutor* rather than a quiz generator: the profile
written at the end of one session has to change how the next one behaves. These
tests pin down that the profile actually reaches the prompt, and that a
first-time learner (or a corrupt profile) does not break the session.
"""

from prompts import (
    GRADER_PROMPT,
    ORCHESTRATOR_PROMPT,
    build_system_prompt,
    card_gen_prompt,
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
    for prompt in (
        ORCHESTRATOR_PROMPT,
        card_gen_prompt(15, grounded=True),
        card_gen_prompt(15, grounded=False),
        GRADER_PROMPT,
    ):
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
    assert "7" in card_gen_prompt(7, grounded=True)


def test_card_gen_prompt_leaves_the_json_braces_literal():
    """The JSON example must survive into the prompt, not be eaten by format()."""
    prompt = card_gen_prompt(7, grounded=True)
    assert '{"cards": [{"front": "...", "back": "...", "topic": "..."}]}' in prompt


def test_card_gen_asks_for_a_mix_of_card_kinds():
    """A deck of nothing but "What is X?" does not test understanding."""
    for grounded in (True, False):
        prompt = card_gen_prompt(30, grounded=grounded).lower()
        assert "recall" in prompt
        assert "applied" in prompt
        assert "diagnostic" in prompt


def test_card_gen_shows_an_example_of_each_kind():
    """Naming the kinds is weaker than describing their shape."""
    prompt = card_gen_prompt(30, grounded=False)
    assert "- recall:" in prompt
    assert "- applied:" in prompt
    assert "- diagnostic:" in prompt


def test_card_gen_states_the_mix_as_counts_not_proportions():
    """Asked for "about a third", the model returned 69% recall cards."""
    prompt = card_gen_prompt(30, grounded=True)
    # 30 cards -> 10 applied, 10 diagnostic, 10 recall.
    assert "10 should be recall" in prompt
    assert "10 applied" in prompt
    assert "10 diagnostic" in prompt


def _mix(max_cards: int) -> tuple[int, int, int]:
    """Read the three requested counts back out of the prompt text."""
    import re

    counts = re.search(
        r"roughly\s+(\d+) should be recall, (\d+) applied, and\s+(\d+) diagnostic",
        card_gen_prompt(max_cards, grounded=True),
    )
    assert counts, f"could not read the mix out of the prompt for {max_cards}"
    recall, applied, diagnostic = (int(n) for n in counts.groups())
    return recall, applied, diagnostic


def test_the_three_counts_sum_to_the_deck_size():
    """A mix that overshoots max_cards would make the prompt contradict its cap.

    Includes the degenerate sizes: a 1-card deck asking for three cards is the
    specific bug this pins down.
    """
    for max_cards in (1, 2, 3, 4, 5, 7, 15, 20, 40):
        assert sum(_mix(max_cards)) == max_cards, max_cards


def test_real_deck_sizes_ask_for_a_genuine_three_way_split():
    """The floor case may degrade, but the sizes actually used must not."""
    for max_cards in (15, 20, 40):
        recall, applied, diagnostic = _mix(max_cards)
        for count in (recall, applied, diagnostic):
            assert count >= max_cards // 4, (max_cards, recall, applied, diagnostic)
        # Recall must no longer dominate the deck the way it did at 69%.
        assert recall <= applied + diagnostic


def test_card_gen_does_not_hand_over_reusable_example_sentences():
    """Given full example cards, the model echoed them back nearly verbatim.

    The diagnostic skeleton is the exception: without a concrete form to copy the
    model produced no diagnostic cards at all, so it keeps a shape — but filled
    with placeholders rather than real Kubernetes content.
    """
    prompt = card_gen_prompt(30, grounded=False)
    assert "ClusterIP" not in prompt
    assert "patterns to fill in, not sentences to reuse" in prompt


def test_diagnostic_cards_must_assert_the_error_not_ask_for_a_verdict():
    """Asked only to "show a flawed example", the model wrote "is this good
    practice?" cards instead — which the student answers by judging, not by
    locating the mistake. That collapses diagnostic into applied."""
    for grounded in (True, False):
        prompt = card_gen_prompt(30, grounded=grounded)
        assert "is this good practice?" in prompt  # named as the thing to avoid
        assert "commit to there being a mistake" in prompt


def test_diagnostic_cards_must_correct_the_falsehood():
    """A wrong statement must never be left as the thing the student memorizes."""
    for grounded in (True, False):
        prompt = card_gen_prompt(30, grounded=grounded)
        assert "state the correction explicitly" in prompt


def test_grounded_prompt_still_allows_invented_scenarios():
    """Otherwise "only what the material states" bans examples outright."""
    prompt = card_gen_prompt(30, grounded=True)
    assert "invent the scenarios" in prompt
    assert "only on facts the" in prompt


def test_grader_grades_reasoning_not_only_recall():
    """Applied and diagnostic answers rarely match the stored wording."""
    prompt = GRADER_PROMPT.lower()
    assert "reasoning" in prompt
    assert "find the mistake" in prompt
    assert "wording differs" in prompt


def test_grader_rejects_agreeing_with_a_false_statement():
    assert "agrees with the false statement" in GRADER_PROMPT


def test_grounded_prompt_forbids_inventing_facts():
    """With material supplied, the student's notes are the syllabus."""
    prompt = card_gen_prompt(20, grounded=True)
    assert "Never invent facts" in prompt
    assert "20" in prompt


def test_topic_prompt_licenses_model_knowledge():
    """Asked to teach a subject, there is no source text to stay inside."""
    prompt = card_gen_prompt(20, grounded=False)
    assert "Never invent facts" not in prompt
    # The phrase wraps across lines in the prompt source, so match on the words
    # rather than the exact span.
    assert "own" in prompt and "knowledge" in prompt
    assert "named a subject" in prompt


def test_both_modes_ask_for_specific_topics():
    """Weak-topic tracking is driven by the tag, so one tag per deck breaks it."""
    for grounded in (True, False):
        assert "several specific topics" in card_gen_prompt(5, grounded=grounded)


def test_both_modes_still_demand_json_only():
    for grounded in (True, False):
        prompt = card_gen_prompt(5, grounded=grounded)
        assert "JSON only" in prompt
        assert '"cards"' in prompt


def test_grader_prompt_documents_the_full_scale():
    for grade in "012345":
        assert grade in GRADER_PROMPT
