"""Where the cascade looks up aliases: Postgres in production, memory in tests."""

from collections import defaultdict
from dataclasses import dataclass
from typing import Protocol

from rapidfuzz import fuzz, process
from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass(frozen=True)
class IngredientRef:
    ingredient_id: int
    cosing_id: str
    inci_name: str


@dataclass(frozen=True)
class AliasHit:
    alias: str
    ingredient_id: int


# Lower is more authoritative: an official INCI name beats a secondary name.
SOURCE_RANK = {"inci": 0, "inci_usa": 1, "inn": 2, "ph_eur": 3, "glossary": 4, "cas": 5}


def _rank(source: str) -> int:
    return SOURCE_RANK.get(source, len(SOURCE_RANK))


class AliasIndex(Protocol):
    def lookup(self, alias: str) -> dict[int, str]:
        """Ingredient IDs with exactly this (normalized) alias, each with its best alias source."""
        ...

    def candidates(self, query: str, k: int) -> list[AliasHit]:
        """Up to k aliases that look similar to `query` (blocking, not final scoring)."""
        ...

    def describe(self, ingredient_id: int) -> IngredientRef: ...


class InMemoryAliasIndex:
    def __init__(self, aliases: dict[str, dict[int, str]], refs: dict[int, IngredientRef]) -> None:
        self._aliases = aliases
        self._refs = refs
        self._keys = list(aliases)

    def lookup(self, alias: str) -> dict[int, str]:
        return dict(self._aliases.get(alias, {}))

    def candidates(self, query: str, k: int) -> list[AliasHit]:
        matches = process.extract(query, self._keys, scorer=fuzz.ratio, limit=k)
        return [AliasHit(alias, i) for alias, _, _ in matches for i in self._aliases[alias]]

    def describe(self, ingredient_id: int) -> IngredientRef:
        return self._refs[ingredient_id]


class PgAliasIndex:
    """Exact lookups from an in-memory copy of ingredient_aliases (fast for batches);
    fuzzy candidates from pg_trgm's trigram index."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._aliases: dict[str, dict[int, str]] = defaultdict(dict)
        for alias, ingredient_id, source in session.execute(
            text("SELECT alias, ingredient_id, source FROM ingredient_aliases")
        ):
            known = self._aliases[alias].get(ingredient_id)
            if known is None or _rank(source) < _rank(known):
                self._aliases[alias][ingredient_id] = source
        self._refs: dict[int, IngredientRef] = {}

    def lookup(self, alias: str) -> dict[int, str]:
        return dict(self._aliases.get(alias, {}))

    def candidates(self, query: str, k: int) -> list[AliasHit]:
        rows = self._session.execute(
            text(
                "SELECT alias, ingredient_id FROM ingredient_aliases "
                "WHERE alias % :q ORDER BY similarity(alias, :q) DESC LIMIT :k"
            ),
            {"q": query, "k": k},
        )
        return [AliasHit(alias, ingredient_id) for alias, ingredient_id in rows]

    def describe(self, ingredient_id: int) -> IngredientRef:
        if ingredient_id not in self._refs:
            cosing_id, inci_name = self._session.execute(
                text("SELECT cosing_id, inci_name FROM ingredients WHERE id = :id"),
                {"id": ingredient_id},
            ).one()
            self._refs[ingredient_id] = IngredientRef(ingredient_id, cosing_id, inci_name)
        return self._refs[ingredient_id]
