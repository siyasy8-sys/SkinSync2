"""Labeling helper: find CosIng IDs for a name.

uv run python -m eval.lookup "hyaluron"
"""

import argparse
import sys

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.resolution.normalize import normalize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search CosIng ingredients by name or alias")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args(argv)
    q = normalize(args.query)
    with get_sessionmaker()() as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT ON (i.cosing_id) i.cosing_id, i.inci_name, i.status, a.alias, "
                "a.source, similarity(a.alias, :q) AS sim "
                "FROM ingredient_aliases a JOIN ingredients i ON i.id = a.ingredient_id "
                "WHERE a.alias LIKE '%' || :q || '%' OR a.alias % :q "
                "ORDER BY i.cosing_id, sim DESC"
            ),
            {"q": q},
        ).all()
    rows = sorted(rows, key=lambda r: -r.sim)[: args.limit]
    for r in rows:
        via = "" if r.source == "inci" else f"  (via {r.source}: {r.alias})"
        print(f"{r.cosing_id:>8}  {r.inci_name}  [{r.status or '?'}]{via}")
    if not rows:
        print("no matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
