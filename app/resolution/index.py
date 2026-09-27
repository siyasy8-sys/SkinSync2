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


class AliasIndex(Protocol):
    def lookup(self, alias: str) -> set[int]:
        """Ingredient IDs with exactly this (normalized) alias."""
        ...

    def candidates(self, query: str, k: int) -> list[AliasHit]:
        """Up to k aliases that look similar to `query` (blocking, not final scoring)."""
        ...

    def describe(self, ingredient_id: int) -> IngredientRef: ...


class InMemoryAliasIndex:
    def __init__(self, aliases: dict[str, set[int]], refs: dict[int, IngredientRef]) -> None:
        self._aliases = aliases
        self._refs = refs
        self._keys = list(aliases)

    def lookup(self, alias: str) -> set[int]:
        return set(self._aliases.get(alias, set()))

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
        self._aliases: dict[str, set[int]] = defaultdict(set)
        for alias, ingredient_id in session.execute(
            text("SELECT alias, ingredient_id FROM ingredient_aliases")
        ):
            self._aliases[alias].add(ingredient_id)
        self._refs: dict[int, IngredientRef] = {}

    def lookup(self, alias: str) -> set[int]:
        return set(self._aliases.get(alias, set()))

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
