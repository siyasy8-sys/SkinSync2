import pytest

from app.resolution.parse import Mention, looks_like_noise, parse_ingredient_list, split_top_level


def _raws(mentions: list[Mention]) -> list[str]:
    return [m.raw for m in mentions]


def test_splits_on_top_level_separators_only() -> None:
    assert split_top_level("a, b (c, d); e") == ["a", " b (c, d)", " e"]
    assert split_top_level("a [b, c], d") == ["a [b, c]", " d"]


def test_positions_follow_label_order_and_strip_header() -> None:
    mentions = parse_ingredient_list("Ingredients: Aqua, Glycerin, Niacinamide.")

    assert _raws(mentions) == ["Aqua", "Glycerin", "Niacinamide"]
    assert [m.position for m in mentions] == [1, 2, 3]


@pytest.mark.parametrize("header", ["INGREDIENTS:", "Ingrédients :", "Zutaten:", "INCI -"])
def test_strips_multilingual_headers(header: str) -> None:
    assert _raws(parse_ingredient_list(f"{header} Aqua, Glycerin")) == ["Aqua", "Glycerin"]


def test_keeps_parentheticals_and_slashes_inside_one_mention() -> None:
    mentions = parse_ingredient_list("Aqua (Water, Eau), Caprylic/Capric Triglyceride")

    assert _raws(mentions) == ["Aqua (Water, Eau)", "Caprylic/Capric Triglyceride"]


def test_ampersand_premix_shares_a_position() -> None:
    mentions = parse_ingredient_list("Water, Butylene Glycol & Rosa Canina Fruit Extract, Glycerin")

    assert [(m.raw, m.position) for m in mentions] == [
        ("Water", 1),
        ("Butylene Glycol", 2),
        ("Rosa Canina Fruit Extract", 2),
        ("Glycerin", 3),
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Aqua, Mica. [+/- CI 77491, CI 77891 (Titanium Dioxide)]",
        "Aqua, Mica, may contain: CI 77491, CI 77891 (Titanium Dioxide)",
        "Aqua, Mica, Peut contenir CI 77491, CI 77891 (Titanium Dioxide)",
    ],
)
def test_may_contain_section_is_flagged(text: str) -> None:
    mentions = parse_ingredient_list(text)

    assert _raws(mentions) == ["Aqua", "Mica", "CI 77491", "CI 77891 (Titanium Dioxide)"]
    assert [m.may_contain for m in mentions] == [False, False, True, True]
    assert [m.position for m in mentions] == [1, 2, 3, 4]


def test_unwraps_bracketed_entries() -> None:
    assert _raws(parse_ingredient_list("Aqua, [Parfum]")) == ["Aqua", "Parfum"]


@pytest.mark.parametrize(
    ("text", "noise"),
    [
        ("Distributed by Rosebud Company Inc. MD 21798 USA", True),
        ("www.example.com", True),
        ("18RO01 0000344 82505", True),
        ("CI 77491", False),
        ("Sodium Hyaluronate", False),
        ("SR-(METHIONYL TRIPEPTIDE-46 HEXAPEPTIDE-40 DECAPEPTIDE-27)", False),
    ],
)
def test_noise_detection_is_conservative(text: str, noise: bool) -> None:
    assert looks_like_noise(text) is noise


def test_empty_and_junk_only_text() -> None:
    assert parse_ingredient_list("") == []
    assert parse_ingredient_list(" , ; . ") == []
