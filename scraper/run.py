"""Orchestrates the daily scrape: Google Store (live) + competitor prices
(best-effort), writes data/latest.json and appends a dated history snapshot.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import competitors
import google_store
from markets import MARKETS

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
HISTORY_DIR = os.path.join(DATA_DIR, "history")


def main():
    now = datetime.now(timezone.utc)
    snapshot = {
        "generated_at": now.isoformat(),
        "markets": {},
    }

    for code in MARKETS:
        print(f"[google_store] scraping {code} ...", file=sys.stderr)
        gs_data = google_store.scrape_market(code)
        print(f"[google_store] {code}: {len(gs_data['products'])} products", file=sys.stderr)

        print(f"[competitors] scraping {code} ...", file=sys.stderr)
        comp_data = competitors.scrape_market(code, gs_data["products"])
        print(f"[competitors] {code}: {len(comp_data)} offers", file=sys.stderr)

        snapshot["markets"][code] = {
            "market_name": MARKETS[code]["name"],
            "currency": MARKETS[code]["currency"],
            "google_store": gs_data["products"],
            "competitors": comp_data,
        }

    os.makedirs(HISTORY_DIR, exist_ok=True)
    latest_path = os.path.join(DATA_DIR, "latest.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    history_path = os.path.join(HISTORY_DIR, f"{now.date().isoformat()}.json")
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"Wrote {latest_path} and {history_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
