"""Text normalization shared by aliases and label mentions.

The same `normalize` is applied to both sides, so an alias and a mention of it
always end up in the same form.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

VariantKind = Literal["full", "no_paren", "paren", "slash_part"]

_SYMBOLS = re.compile(r"[®™©*†‡°]")
_NANO = re.compile(r"[\[(]\s*nano\s*[\])]")
_CONCENTRATION = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*%")
_SPACED_SLASH = re.compile(r"\s*/\s*")
_SPACED_HYPHEN = re.compile(r"\s+-\s*|\s*-\s+")
_PAREN_GROUP = re.compile(r"[(\[]([^()\[\]]*(?:[(\[][^()\[\]]*[)\]][^()\[\]]*)*)[)\]]")
_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = " .,;:-_"


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Lowercase, accent-free, symbol-free form with concentrations removed.

    Also closes spacing around "/" and "-", which label text breaks apart
    ("caprylic/ capric", "phenoxyeth - anol", "peg - 8").
    """
    t = strip_accents(unicodedata.normalize("NFKC", text)).lower()
    t = _SYMBOLS.sub("", t)
    t = _NANO.sub("", t)
    t = _CONCENTRATION.sub("", t)
    t = _SPACED_SLASH.sub("/", t.replace("\\", "/"))  # "water\\aqua\\eau" uses backslashes
    t = _SPACED_HYPHEN.sub("-", t)
    t = _WHITESPACE.sub(" ", t)
    return t.strip(_EDGE_PUNCTUATION)


@dataclass(frozen=True)
class Variant:
    text: str
    kind: VariantKind


def _without_parens(text: str) -> str:
    previous = None
    while previous != text:  # repeat to remove nested groups from the inside out
        previous = text
        text = re.sub(r"[(\[][^()\[\]]*[)\]]", " ", text)
    return _WHITESPACE.sub(" ", text).strip(_EDGE_PUNCTUATION)


def variants(normalized: str) -> list[Variant]:
    """Candidate forms of a normalized mention, most faithful first.

    1. the full string (keeps INCI names that contain "/", e.g. caprylic/capric triglyceride)
    2. without parentheticals ("zea mays (corn) starch" -> "zea mays starch")
    3. each parenthetical's contents ("ci 77891 (titanium dioxide)" -> "titanium dioxide")
    4. slash parts ("aqua/water/eau" -> "aqua", "water", "eau")
    """
    found: list[Variant] = [Variant(normalized, "full")]
    bare = _without_parens(normalized)
    found.append(Variant(bare, "no_paren"))
    for group in _PAREN_GROUP.findall(normalized):
        inner = group.strip(_EDGE_PUNCTUATION)
        found.append(Variant(inner, "paren"))
        found.append(Variant(_without_parens(inner), "paren"))
    for source in (bare, *(v.text for v in found if v.kind == "paren")):
        parts = [p.strip(_EDGE_PUNCTUATION) for p in source.split("/")]
        if len(parts) > 1:
            found.extend(Variant(p, "slash_part") for p in parts)

    unique: dict[str, Variant] = {}
    for variant in found:
        if variant.text and variant.text not in unique:
            unique[variant.text] = variant
    return list(unique.values())
