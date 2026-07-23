#!/usr/bin/env python3
"""
export_players_pool.py — export the auction DB's `players` table to the committed
registry/auction_players.json.gz that build_registry.py reads WHEN THE AUCTION DB IS ABSENT.

Why this exists: build_registry anchors squad names to cricsheet_ids using the auction DB's
`players` table (= cricsheet's people registry: cricsheet_id + name spellings + country +
gender). That DB is 61MB and gitignored, so it isn't in the CI checkout — which made cricapi
auto-ingest tours fail to anchor in CI (empty DB → `select ... from players` crashes). The
`players` table alone is tiny (≈10k rows → ~0.2MB gzipped), so we commit just that as the CI
fallback. build_registry.open_pool_con() prefers the live DB locally and this export in CI.

Run this LOCALLY (where the auction DB exists) whenever the auction's player set materially
changes (e.g. after ingesting a new league's squads), then commit the regenerated .gz:

    python3 registry/export_players_pool.py
    git add registry/auction_players.json.gz && git commit -m "refresh CI player pool"

Env: AUCTION_DB (defaults to the local auction path, same as build_registry.py).
"""
import os, sys, json, gzip, sqlite3

HERE = os.path.dirname(os.path.abspath(__file__))
AUCTION_DB = os.environ.get("AUCTION_DB",
    "/Users/nishant-singodia/cricket-auction-helper/db/cricket-auction.db")
OUT = os.path.join(HERE, "auction_players.json.gz")

# EXACTLY the columns build_registry.db_pool selects — keep this list in sync with it.
COLS = ["id", "cricsheet_id", "name", "full_name", "country", "gender"]


def main():
    if not os.path.exists(AUCTION_DB):
        sys.exit(f"auction DB not found at {AUCTION_DB} — set AUCTION_DB or run where the DB lives.")
    con = sqlite3.connect(AUCTION_DB)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(f"select {', '.join(COLS)} from players")]
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False)
    size_mb = os.path.getsize(OUT) / 1e6
    with_cs = sum(1 for r in rows if r.get("cricsheet_id"))
    print(f"exported {len(rows)} players ({with_cs} with cricsheet_id) -> {OUT} ({size_mb:.2f} MB)")
    print("commit registry/auction_players.json.gz so CI can anchor cricapi auto-tours.")


if __name__ == "__main__":
    main()
