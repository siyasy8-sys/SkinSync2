"""Rule-based candidate links between distinct ingredients (salts, esters).

Salts and esters stay separate canonical ingredients: tocopheryl acetate is
not tocopherol. These links let retrieval widen to a parent's evidence later,
but they are only candidates (reviewed=false) until a human confirms them.
"""

import re
from dataclasses import dataclass
from typing import Literal

RelationKind = Literal["salt_of", "ester_of"]

_SALT = re.compile(
    r"^(?:sodium|disodium|trisodium|potassium|dipotassium|calcium|magnesium|zinc|ammonium) "
    r"(\w+)ate$"
)
_ESTER = re.compile(
    r"^(\w+)yl (?:acetate|palmitate|propionate|linoleate|nicotinate|succinate|stearate|laurate|"
    r"oleate|myristate|benzoate|salicylate)$"
)


@dataclass(frozen=True)
class RelationCandidate:
    ingredient: str  # normalized INCI name of the salt/ester
    related: str  # normalized INCI name of the parent
    kind: RelationKind


def candidate_parents(name: str) -> list[tuple[str, RelationKind]]:
    """Parent names a salt/ester might derive from; existence is checked by the caller."""
    if m := _SALT.match(name):
        return [(f"{m.group(1)}ic acid", "salt_of")]
    if m := _ESTER.match(name):
        stem = m.group(1)
        return [(f"{stem}ol", "ester_of"), (f"{stem}ic acid", "ester_of")]
    return []


def find_relations(names: set[str]) -> list[RelationCandidate]:
    """Links whose parent also exists in the canonical list (normalized INCI names)."""
    found = []
    for name in sorted(names):
        for parent, kind in candidate_parents(name):
            if parent in names and parent != name:
                found.append(RelationCandidate(name, parent, kind))
                break
    return found
