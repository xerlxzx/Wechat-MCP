import pytest


@pytest.mark.parametrize("text,expected", [
    ("  Ａｂｃ \t\n 发票  ", "abc 发票"),
    ("", ""),
    ("   ", ""),
    ("ＣＡＦＥ́", "café"),
    ("ﬁ", "fi"),
    ("ǅ", "dž"),
])
def test_normalize_folds_width_case_and_whitespace(text, expected):
    from wechat_bridge.search.tokenize import normalize
    assert normalize(text) == expected


@pytest.mark.parametrize("text", ["ＡＢＣ  x", "café", "ẞ ß", "한국어 ᄒᆞᆫ", "🐈‍⬛ ok"])
def test_normalize_is_idempotent(text):
    from wechat_bridge.search.tokenize import normalize
    assert normalize(normalize(text)) == normalize(text)


def test_terms_are_unigrams_and_adjacent_bigrams_only():
    from wechat_bridge.search.tokenize import terms
    assert terms("ab") == ("b61x62", "u61", "u62")
    assert terms("") == ()
    # Astral code points are one unit, not two surrogates.
    assert terms("🐈") == ("u1f408",)


def test_single_character_query_falls_back_to_unigrams_and_binds_generation_and_kind():
    from wechat_bridge.search.tokenize import match_expression
    normalized, expr = match_expression(" Ａ ", 7, "messages")
    assert normalized == "a"
    assert expr == '"z7" AND "kmessages" AND "u61"'
    _, expr = match_expression("ab", 3, "contacts")
    assert expr == '"z3" AND "kcontacts" AND "b61x62"'


def test_match_expression_cannot_be_injected_through_query_text():
    from wechat_bridge.search.tokenize import match_expression
    _, expr = match_expression('" OR 1=1 --', 1, "messages")
    # Every query character is encoded as a hex token; no raw quote or operator survives.
    assert expr.count('"') == 2 * (expr.count(" AND ") + 1)
    assert "OR" not in expr.replace("kmessages", "")


@pytest.mark.parametrize("text,offset,expected", [
    ("abc", 0, 0), ("abc", 2, 2), ("abc", 3, 3),
    ("a  b", 1, 1), ("a  b", 2, 3),       # collapsed whitespace run maps to its start / next char
    ("café!", 3, 3), ("café!", 4, 5),  # combining mark stays with its starter
    ("ＡＢ", 1, 1),
    ("", 0, 0),
])
def test_source_offset_maps_normalized_position_to_source(text, offset, expected):
    from wechat_bridge.search.tokenize import source_offset
    assert source_offset(text, offset) == expected
