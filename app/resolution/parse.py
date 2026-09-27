"""Splits a product's raw ingredient text into ordered mentions."""

import re
from dataclasses import dataclass

_HEADER = re.compile(
    r"^\s*(?:ingredients?|ingr[eé]dients?|ingrediënten|zutaten|ingredientes|ingredienti|"
    r"composition|inci|sk[lł]adniki|innehåll)\s*[:\-\u2013]\s*",
    re.IGNORECASE,
)
_MAY_CONTAIN = re.compile(
    r"\+\s*/\s*-|±|may contain|peut contenir|kann enthalten|puede contener|può contenere",
    re.IGNORECASE,
)
_LEADING_MARKER = re.compile(r"\s*[/:]?\s*(?:" + _MAY_CONTAIN.pattern + r")\s*[:]?", re.IGNORECASE)
_NOISE = re.compile(
    r"https?://|www\.|distribu|made in|fabriqu|manufactur|\blot\b|batch|\bexp\b|@",
    re.IGNORECASE,
)
_COLOUR_INDEX = re.compile(r"^\s*(?:ci|e)\s?\d{3,5}\b", re.IGNORECASE)
_SEPARATORS = ",;•\n"
_OPEN, _CLOSE = "([{", ")]}"
_AMPERSAND = re.compile(r"\s+&\s+")


@dataclass(frozen=True)
class Mention:
    raw: str
    position: int  # 1-based label order; parts of an "&" premix share a position
    may_contain: bool = False
    suspected_noise: bool = False


def split_top_level(text: str) -> list[str]:
    """Splits on separators that are not inside (), [] or {}.

    Line breaks only separate entries when the text has no commas/semicolons;
    otherwise they are label wrapping ("Sodium\nHyaluronate") and become spaces.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if "," in text or ";" in text:
        text = text.replace("\n", " ")
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for i, char in enumerate(text):
        if char in _OPEN:
            depth += 1
        elif char in _CLOSE:
            depth = max(depth - 1, 0)
        # "1,2-Hexanediol": a comma between digits is part of a chemical name.
        in_number = (
            char == ","
            and 0 < i < len(text) - 1
            and text[i - 1].isdigit()
            and text[i + 1].isdigit()
        )
        if char in _SEPARATORS and depth == 0 and not in_number:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def looks_like_noise(text: str) -> bool:
    """Conservative: label text that isn't an ingredient (addresses, batch codes)."""
    if _NOISE.search(text):
        return True
    if _COLOUR_INDEX.match(text):
        return False  # "CI 77491" is a real INCI colorant name
    letters = sum(c.isalpha() for c in text)
    digits = sum(c.isdigit() for c in text)
    return digits > letters or len(text.split()) > 12


def _split_may_contain(text: str) -> tuple[str, str]:
    """(definite part, may-contain part). The marker may open a bracket: "[+/- CI 77891]"."""
    match = _MAY_CONTAIN.search(text)
    if not match:
        return text, ""
    head, tail = text[: match.start()], text[match.end() :]
    stripped = head.rstrip()
    if stripped.endswith(("[", "(")):
        opener = stripped[-1]
        head = stripped[:-1]
        closer = _CLOSE[_OPEN.index(opener)]
        cut = tail.rfind(closer)
        if cut != -1:
            tail = tail[:cut] + tail[cut + 1 :]
    # Markers are often stacked: "[+/- MAY CONTAIN / PEUT CONTENIR CI 77491]".
    while stacked := _LEADING_MARKER.match(tail):
        tail = tail[stacked.end() :]
    return head, tail.lstrip(" :")


def _unwrap(text: str) -> str:
    """Drops brackets that wrap a whole entry: "[CI 77891]" -> "CI 77891"."""
    while len(text) > 1 and text[0] in "[{" and text[-1] == _CLOSE[_OPEN.index(text[0])]:
        text = text[1:-1].strip(" .:")
    return text


def parse_ingredient_list(text: str) -> list[Mention]:
    definite, may_contain = _split_may_contain(_HEADER.sub("", text.strip()))
    mentions: list[Mention] = []
    position = 0
    for chunk, is_optional in ((definite, False), (may_contain, True)):
        for part in split_top_level(chunk):
            cleaned = _unwrap(part.strip(" .:"))
            if not cleaned:
                continue
            position += 1
            for piece in _AMPERSAND.split(cleaned):
                piece = piece.strip(" .:")
                if piece:
                    mentions.append(
                        Mention(
                            raw=piece,
                            position=position,
                            may_contain=is_optional,
                            suspected_noise=looks_like_noise(piece),
                        )
                    )
    return mentions
