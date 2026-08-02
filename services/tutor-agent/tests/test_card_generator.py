from conftest import FakeLLM

from card_generator import generate_cards


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
