"""Builds alias rows for a canonical ingredient from its CosIng names and CAS numbers."""

import re
from dataclasses import dataclass

from app.resolution.normalize import normalize

_CAS = re.compile(r"(?<![\d-])(\d{2,7})-(\d{2})-(\d)(?![\d-])")

# Priority when the same normalized name comes from several fields.
NAME_SOURCES = ("inci", "inci_usa", "inn", "ph_eur", "glossary")


@dataclass(frozen=True)
class AliasCandidate:
    alias: str
    source: str
    confidence: float = 1.0


def cas_checksum_ok(first: str, second: str, check: str) -> bool:
    """CAS check digit: digits before it, weighted 1, 2, 3... from the right, mod 10."""
    digits = (first + second)[::-1]
    return sum(i * int(d) for i, d in enumerate(digits, start=1)) % 10 == int(check)


def parse_cas(text: str | None) -> list[str]:
    """Every valid CAS number in CosIng's free-text CAS field, in order, deduped.

    The field mixes separators and notes, e.g. "54-28-4 (gamma)/ 16698-35-4(beta) / ...".
    Numbers failing the check digit are dropped rather than trusted.
    """
    if not text:
        return []
    found: dict[str, None] = {}
    for first, second, check in _CAS.findall(text):
        if cas_checksum_ok(first, second, check):
            found[f"{first}-{second}-{check}"] = None
    return list(found)


def build_aliases(
    inci_name: str, names: dict[str, list[str]], cas_number: str | None
) -> list[AliasCandidate]:
    """One row per distinct normalized name, plus one per valid CAS number."""
    by_alias: dict[str, AliasCandidate] = {}
    sources = {"inci": [inci_name], **names}
    for source in NAME_SOURCES:
        for name in sources.get(source, []):
            alias = normalize(name)
            if alias and alias not in by_alias:
                by_alias[alias] = AliasCandidate(alias, source)
    for cas in parse_cas(cas_number):
        by_alias.setdefault(cas, AliasCandidate(cas, "cas"))
    return list(by_alias.values())
