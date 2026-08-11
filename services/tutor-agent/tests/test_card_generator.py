from fakes import FakeLLM

from card_generator import generate_cards, is_topic_request

MATERIAL = (
    "Mitochondria are the powerhouse of the cell, producing ATP through cellular "
    "respiration. Ribosomes synthesize proteins from mRNA."
)
"""Real material: long enough, and punctuated, so it is never mistaken for a
'teach me X' request."""


def test_generates_valid_cards():
    llm = FakeLLM(['{"cards":[{"front":"Q1","back":"A1","topic":"bio"}]}'])
    cards = generate_cards("some material", llm)
    assert cards == [{"front": "Q1", "back": "A1", "topic": "bio"}]


def test_malformed_then_retry_succeeds():
    llm = FakeLLM(["not json", '{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    cards = generate_cards("m", llm)
    assert len(cards) == 1
    assert llm.calls == 2


def test_all_malformed_returns_empty_not_crash():
    llm = FakeLLM(["nope", "still nope"])
    assert generate_cards("m", llm) == []


def test_extracts_json_from_prose_wrapper():
    """Small models often wrap JSON in chatter or a code fence."""
    llm = FakeLLM(
        ['Sure! Here are your cards:\n```json\n{"cards":[{"front":"Q","back":"A","topic":"t"}]}\n```\nEnjoy!']
    )
    assert len(generate_cards("m", llm)) == 1


def test_keeps_valid_cards_and_drops_invalid_ones():
    """A partly-bad batch should not cost the learner the good cards."""
    llm = FakeLLM(
        ['{"cards":[{"front":"Q1","back":"A1","topic":"t"},'
         '{"front":"","back":"A2","topic":"t"},'
         '{"back":"A3","topic":"t"}]}']
    )
    cards = generate_cards("m", llm)
    assert [c["front"] for c in cards] == ["Q1"]


def test_bare_list_response_is_accepted():
    """Accept a top-level list as well as {"cards": [...]}."""
    llm = FakeLLM(['[{"front":"Q","back":"A","topic":"t"}]'])
    assert len(generate_cards("m", llm)) == 1


def test_respects_max_cards():
    many = ",".join(
        f'{{"front":"Q{i}","back":"A{i}","topic":"t"}}' for i in range(20)
    )
    llm = FakeLLM([f'{{"cards":[{many}]}}'])
    cards = generate_cards("m", llm, max_cards=5)
    assert len(cards) == 5


def test_llm_exception_returns_empty_not_crash():
    llm = FakeLLM([RuntimeError("bedrock exploded")])
    assert generate_cards("m", llm) == []


def test_material_is_passed_to_the_model():
    llm = FakeLLM(['{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    generate_cards("mitochondria are the powerhouse", llm)
    assert "mitochondria" in str(llm.received[0])


def test_retry_prompt_is_stricter_than_the_first():
    llm = FakeLLM(["garbage", '{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    generate_cards("m", llm)
    assert llm.calls == 2
    assert str(llm.received[1]) != str(llm.received[0])


def test_strips_whitespace_from_fields():
    llm = FakeLLM(['{"cards":[{"front":"  Q  ","back":" A ","topic":" bio "}]}'])
    cards = generate_cards("m", llm)
    assert cards[0] == {"front": "Q", "back": "A", "topic": "bio"}


def test_empty_material_returns_empty_without_calling_model():
    llm = FakeLLM(['{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    assert generate_cards("   ", llm) == []
    assert llm.calls == 0


# --- Choosing between grounded material and "teach me X" -------------------


def test_teach_me_phrasing_is_a_topic_request():
    for text in (
        "teach me Kubernetes",
        "Teach me about the Ottoman Empire",
        "explain photosynthesis",
        "I want to learn Rust",
        "quiz me on organic chemistry",
        "basics of linear algebra",
    ):
        assert is_topic_request(text), text


def test_bare_subject_name_is_a_topic_request():
    """A few words with no facts in them cannot be material."""
    assert is_topic_request("Kubernetes networking")
    assert is_topic_request("Ottoman tax reform")


def test_real_material_is_not_a_topic_request():
    assert not is_topic_request(MATERIAL)


def test_long_text_is_material_even_if_it_says_explain():
    """Notes that discuss explaining something are still notes."""
    text = (
        "In this chapter we explain how the sodium-potassium pump maintains the "
        "resting membrane potential. The pump moves three sodium ions out for "
        "every two potassium ions in, which leaves the interior negative. "
        "This gradient is what an action potential later exploits."
    )
    assert not is_topic_request(text)


def test_short_punctuated_prose_is_material():
    """A single real sentence is material, not a subject name."""
    assert not is_topic_request("The nucleus stores DNA.")


def test_topic_request_prompt_allows_model_knowledge():
    llm = FakeLLM(['{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    generate_cards("teach me Kubernetes", llm)
    sent = str(llm.received[0])
    assert "Never invent facts" not in sent
    assert "Subject to teach" in sent


def test_material_prompt_stays_grounded():
    llm = FakeLLM(['{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    generate_cards(MATERIAL, llm)
    sent = str(llm.received[0])
    assert "Never invent facts" in sent
    assert "Study material" in sent


def test_grounded_can_be_forced_for_a_short_input():
    """An explicit flag beats the heuristic."""
    llm = FakeLLM(['{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    generate_cards("teach me Kubernetes", llm, grounded=True)
    assert "Never invent facts" in str(llm.received[0])


# --- Truncated replies ------------------------------------------------------


def _unclosed_json(count):
    """A reply cut off mid-JSON, as a max_tokens truncation produces."""
    cards = ",".join(
        f'{{"front":"Question number {i} about the subject","back":"A fairly '
        f'long answer for card {i} so the reply exceeds the length floor",'
        f'"topic":"topic{i}"}}'
        for i in range(count)
    )
    return '{"cards":[' + cards + ',{"front":"Question that never fi'


def test_truncated_reply_retries_with_a_smaller_deck():
    """A low output cap must degrade to a shorter deck, not an empty one."""
    llm = FakeLLM(
        [
            _unclosed_json(12),
            '{"cards":[{"front":"Q","back":"A","topic":"t"}]}',
        ]
    )
    cards = generate_cards(MATERIAL, llm, max_cards=40)
    assert len(cards) == 1
    assert llm.calls == 2
    # The retry must ask for fewer cards than the request that was cut off.
    assert "40" not in str(llm.received[1])


def test_truncation_retry_has_a_floor():
    """It should stop halving rather than ask for a deck too thin to study."""
    llm = FakeLLM([_unclosed_json(12)] * 3)
    assert generate_cards(MATERIAL, llm, max_cards=40) == []
    # Two halvings (40 -> 20 -> 10) then stop, rather than looping forever.
    assert llm.calls == 3


def test_short_garbage_is_not_treated_as_truncation():
    """A brief malformed reply is a formatting failure, not a length one."""
    llm = FakeLLM(["nope", '{"cards":[{"front":"Q","back":"A","topic":"t"}]}'])
    cards = generate_cards(MATERIAL, llm, max_cards=40)
    assert len(cards) == 1
    # The deck size was not reduced, because the reply was not truncated.
    assert "40" in str(llm.received[1])
