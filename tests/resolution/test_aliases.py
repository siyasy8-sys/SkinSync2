import pytest

from app.resolution.aliases import build_aliases, cas_checksum_ok, parse_cas

TOCOPHEROL_CAS = (
    "54-28-4 (gamma)/ 16698-35-4(beta) / 10191-41-0(DL) / 119-13-1 / 1406-18-4 / "
    "1406-66-2 / 2074-53-5 (DL) / 59-02-9 (D)/7616-22-0"
)


@pytest.mark.parametrize(
    ("cas", "ok"),
    [("98-92-0", True), ("7732-18-5", True), ("9004-61-9", True), ("98-92-1", False)],
)
def test_cas_checksum(cas: str, ok: bool) -> None:
    assert cas_checksum_ok(*cas.split("-")) is ok


def test_parse_cas_handles_cosing_free_text() -> None:
    assert parse_cas(TOCOPHEROL_CAS) == [
        "54-28-4",
        "16698-35-4",
        "10191-41-0",
        "119-13-1",
        "1406-18-4",
        "1406-66-2",
        "2074-53-5",
        "59-02-9",
        "7616-22-0",
    ]
    assert parse_cas("34354-88-6/100403-19-8 (generic)") == ["34354-88-6", "100403-19-8"]


def test_parse_cas_drops_invalid_and_placeholders() -> None:
    assert parse_cas("98-92-1") == []  # bad check digit
    assert parse_cas("-") == []
    assert parse_cas(None) == []
    assert parse_cas("98-92-0 / 98-92-0") == ["98-92-0"]


def test_build_aliases_normalizes_dedupes_and_prefers_inci() -> None:
    aliases = build_aliases(
        "AQUA",
        {"inci_usa": ["WATER"], "ph_eur": ["Aqua"], "glossary": ["AQUA"]},
        "7732-18-5",
    )

    assert [(a.alias, a.source) for a in aliases] == [
        ("aqua", "inci"),
        ("water", "inci_usa"),
        ("7732-18-5", "cas"),
    ]
