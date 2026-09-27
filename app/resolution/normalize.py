"""Text normalization shared by aliases and label mentions.

The same `normalize` is applied to both sides, so an alias and a mention of it
always end up in the same form.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

VariantKind = Literal["full", "no_paren", "paren", "slash_part", "clause"]

_SYMBOLS = re.compile(r"[®™©*†‡°]")
_NANO = re.compile(r"[\[(]\s*nano\s*[\])]")
_CONCENTRATION = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?\s*%")
_SPACED_SLASH = re.compile(r"\s*/\s*")
_SPACED_HYPHEN = re.compile(r"\s+-\s*|\s*-\s+")
_PAREN_GROUP = re.compile(r"[(\[]([^()\[\]]*(?:[(\[][^()\[\]]*[)\]][^()\[\]]*)*)[)\]]")
_WHITESPACE = re.compile(r"\s+")
_EDGE_PUNCTUATION = " .,;:-_/\\"
# Cyrillic and Greek letters that look like Latin ones (a Cyrillic "\u0410" in "AQUA").
# Written as escapes so the source itself has no look-alikes.
_CONFUSABLES = str.maketrans(
    {
        "\u0410": "A",
        "\u0412": "B",
        "\u0421": "C",
        "\u0415": "E",
        "\u041d": "H",
        "\u0406": "I",
        "\u0408": "J",
        "\u041a": "K",
        "\u041c": "M",
        "\u041e": "O",
        "\u0420": "P",
        "\u0405": "S",
        "\u0422": "T",
        "\u0425": "X",
        "\u0423": "Y",
        "\u0430": "a",
        "\u0435": "e",
        "\u0441": "c",
        "\u0456": "i",
        "\u0458": "j",
        "\u043e": "o",
        "\u0440": "p",
        "\u0455": "s",
        "\u0445": "x",
        "\u0443": "y",
        "\u0391": "A",
        "\u0392": "B",
        "\u0395": "E",
        "\u0396": "Z",
        "\u0397": "H",
        "\u0399": "I",
        "\u039a": "K",
        "\u039c": "M",
        "\u039d": "N",
        "\u039f": "O",
        "\u03a1": "P",
        "\u03a4": "T",
        "\u03a5": "Y",
        "\u03a7": "X",
    }
)
_LATIN = re.compile(r"[A-Za-z]")
_COLOUR_INDEX = re.compile(r"\bc[i1l]\s?(\d{5})\b")  # "CI77492", "C1 15510" -> "ci 77492"
# Manufacturer formula codes: "(CODE F.I.L. D235677/1)", "F.I.L#C16232/5", "FIL 1837".
_FORMULA_CODE = re.compile(
    r"\(?\s*(?:code\s*)?\bf\.?\s?i\.?\s?l\.?(?:\s*(?:n°|no\.?|[#:]))?\s*[a-z]?\d[\w./-]*\s*\)?"
)
_SENTENCE_BREAK = re.compile(r"\.\s+")
_FUNCTION_WORDS = re.compile(r"\b(?:in|a|an|the|of|to|for|from|with|is|are|use|store|keep|avoid)\b")


def _fix_confusables(text: str) -> str:
    """Maps look-alike Cyrillic/Greek letters to Latin, but only in words that also
    contain Latin letters, so genuinely Cyrillic or Greek words are left alone."""
    return " ".join(
        word.translate(_CONFUSABLES) if _LATIN.search(word) else word for word in text.split(" ")
    )


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Lowercase, accent-free, symbol-free form with concentrations removed.

    Also closes spacing around "/" and "-", which label text breaks apart
    ("caprylic/ capric", "phenoxyeth - anol", "peg - 8").
    """
    t = _fix_confusables(unicodedata.normalize("NFKC", text))
    t = strip_accents(t).lower()
    t = _FORMULA_CODE.sub(" ", t)
    t = _COLOUR_INDEX.sub(r"ci \1", t)
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


def _clauses(text: str) -> list[str]:
    out: list[str] = []
    if ":" in text:
        # Right side first: the left side is usually a header ("fragrance: ...", "inci: ...").
        out.extend(part.strip(_EDGE_PUNCTUATION) for part in reversed(text.split(":")))
    head, *rest = _SENTENCE_BREAK.split(text, maxsplit=1)
    # Only cut off prose ("storage: store in a cool place"). A tail without English
    # function words is more likely the next ingredient after a missing comma.
    if rest and _FUNCTION_WORDS.search(rest[0]):
        out.append(head.strip(_EDGE_PUNCTUATION))
    return out


def compact(text: str) -> str:
    """Spaces and hyphens removed: labels break and join words at random
    ("sod ium hyaluronate", "propyl paraben", "dipentaery thrityl")."""
    return re.sub(r"[\s-]+", "", text)


def variants(normalized: str) -> list[Variant]:
    """Candidate forms of a normalized mention, most faithful first.

    1. the full string (keeps INCI names that contain "/", e.g. caprylic/capric triglyceride)
    2. without parentheticals ("zea mays (corn) starch" -> "zea mays starch")
    3. each parenthetical's contents ("ci 77891 (titanium dioxide)" -> "titanium dioxide")
    4. slash parts ("aqua/water/eau" -> "aqua", "water", "eau")
    5. clauses: each side of a colon ("fragrance: butylphenyl methylpropional"), and the
       first sentence when the rest is prose ("glyceryl caprylate. storage: store in a cool")
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
    for clause in _clauses(bare):
        found.append(Variant(clause, "clause"))

    unique: dict[str, Variant] = {}
    for variant in found:
        if variant.text and variant.text not in unique:
            unique[variant.text] = variant
    return list(unique.values())
