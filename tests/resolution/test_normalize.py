import pytest

from app.resolution.normalize import normalize, variants


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("AQUA", "aqua"),
        ("Glycérine", "glycerine"),
        ("Benzoyl Peroxide 5%", "benzoyl peroxide"),
        ("Uncoated Zinc Oxide 22.5 %", "uncoated zinc oxide"),
        ("Titanium Dioxide [nano]", "titanium dioxide"),
        ("Aloe Barbadensis Leaf Juice*", "aloe barbadensis leaf juice"),
        ("Sodium Hyaluronate®", "sodium hyaluronate"),
        ("Caprylic/ Capric Triglyceride", "caprylic/capric triglyceride"),
        ("Water\\Aqua\\Eau", "water/aqua/eau"),
        ("Phenoxyeth - anol", "phenoxyeth-anol"),
        ("PEG - 8", "peg-8"),
        ("  Parfum.  ", "parfum"),
        ("C12-15 Alkyl Benzoate", "c12-15 alkyl benzoate"),
    ],
)
def test_normalize(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_normalize_is_idempotent() -> None:
    for raw in ["Aqua (Water/Eau)", "Benzoyl Peroxide 5%", "CI 77891 (Titanium Dioxide)"]:
        assert normalize(normalize(raw)) == normalize(raw)


def _texts(normalized: str) -> list[tuple[str, str]]:
    return [(v.text, v.kind) for v in variants(normalized)]


def test_full_form_comes_first_so_slashed_inci_names_survive() -> None:
    assert _texts("caprylic/capric triglyceride")[0] == ("caprylic/capric triglyceride", "full")


def test_parenthetical_variants() -> None:
    assert _texts("ci 77891 (titanium dioxide)") == [
        ("ci 77891 (titanium dioxide)", "full"),
        ("ci 77891", "no_paren"),
        ("titanium dioxide", "paren"),
    ]


def test_nested_parentheticals_and_slashes() -> None:
    assert _texts("aqua (water/eau)") == [
        ("aqua (water/eau)", "full"),
        ("aqua", "no_paren"),
        ("water/eau", "paren"),
        ("water", "slash_part"),
        ("eau", "slash_part"),
    ]
    assert ("water", "paren") in _texts("aqua (water (eau))")


def test_slash_parts() -> None:
    assert _texts("aqua/water/eau") == [
        ("aqua/water/eau", "full"),
        ("aqua", "slash_part"),
        ("water", "slash_part"),
        ("eau", "slash_part"),
    ]
