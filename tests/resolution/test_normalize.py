import pytest

from app.resolution.normalize import compact, normalize, variants


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


RUSSIAN_WORD = "".join(
    chr(c) for c in (0x421, 0x44B, 0x432, 0x43E, 0x440, 0x43E, 0x442, 0x43A, 0x430)
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\N{CYRILLIC CAPITAL LETTER A}QUA", "aqua"),  # Cyrillic A in a Latin word
        ("CAR\N{CYRILLIC CAPITAL LETTER VE}\N{CYRILLIC CAPITAL LETTER O}MER", "carbomer"),
        (RUSSIAN_WORD, RUSSIAN_WORD.lower()),  # a genuinely Cyrillic word is left alone
        ("CI77492", "ci 77492"),
        ("C1 15510 (orange 4)", "ci 15510 (orange 4)"),
        ("Ethylhexylglycerin (CODE F.I.L. D235677/1)", "ethylhexylglycerin"),
        ("PARFUM F.I.L#C16232/5", "parfum"),
        ("Citric Acid. FIL 1837", "citric acid"),
        ("Filipendula Ulmaria Extract", "filipendula ulmaria extract"),  # "fil" inside a word
        ("/ Aloe Barbadensis Leaf Juice /", "aloe barbadensis leaf juice"),
    ],
)
def test_normalize_label_noise(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def _clauses(normalized: str) -> list[str]:
    return [v.text for v in variants(normalized) if v.kind == "clause"]


def test_clause_variants() -> None:
    assert _clauses("fragrance: butylphenyl methylpropional") == [
        "butylphenyl methylpropional",
        "fragrance",
    ]
    assert "glyceryl caprylate" in _clauses("glyceryl caprylate. storage: store in a cool place")
    assert _clauses("xanthan gum. lecithin") == []  # a short tail may be a real ingredient
    # A missing comma, not prose: don't pluck out the first name.
    assert _clauses("acetylated lanolin alcohol. butyrospermum parkii butter extract") == []


def test_compact() -> None:
    assert compact("sod ium hyaluronate") == compact("sodium hyaluronate") == "sodiumhyaluronate"
    assert compact("propyl paraben") == compact("propylparaben")
