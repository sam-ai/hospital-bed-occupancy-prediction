"""Append-only backfill: add N earlier days of dummy snapshots per ward.

WHY: `generate_scenario_data` always ends at "now" and counts backward by
`days`. To extend history *earlier* (before the existing window) WITHOUT
overwriting current data, this script generates a short series then shifts
every timestamp back by `--shift-days` so it lands before the existing data.

SAFETY:
  - It ONLY inserts new snapshot docs (deterministic ids from shifted
    timestamps). It performs NO deletes and does NOT touch forecasts.
  - By default it runs in DRY-RUN mode and just reports what it *would* add.
    Pass --commit to actually write.

Usage:
    # Preview only (no writes):
    uv run python scripts/backfill_earlier_days.py --days 7 --shift-days 30
    # Actually write:
    uv run python scripts/backfill_earlier_days.py --days 7 --shift-days 30 --commit
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk

from app.data.mock_regimes import SCENARIOS, generate_scenario_data
from app.data.wards import WARDS

HOSPITAL_ID = "HOSPITAL-MAIN-01"
ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")


def _doc_id(s: dict) -> str:
    return (
        f"{s.get('hospital_id', 'UNKNOWN')}_"
        f"{s.get('census', {}).get('unit_id', 'UNKNOWN')}_"
        f"{s.get('timestamp', 'NO_TS').replace(':', '-').replace('.', '-')}"
    )


def _shift_timestamp(ts_iso: str, delta: timedelta) -> str:
    dt = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    return (dt - delta).isoformat()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Append earlier-day snapshots (no overwrite).")
    parser.add_argument("--scenario", default="balanced", choices=list(SCENARIOS))
    parser.add_argument("--days", type=int, default=7, help="How many earlier days to add.")
    parser.add_argument("--shift-days", type=int, default=30,
                        help="Shift generated timestamps back by this many days "
                             "so they land before existing data.")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write. Without this it's a dry run.")
    args = parser.parse_args()

    delta = timedelta(days=args.shift_days)
    mode = "COMMIT" if args.commit else "DRY-RUN"
    print(f"[*] ES={ES_URL}  scenario={args.scenario}  days={args.days}  "
          f"shift_back={args.shift_days}d  mode={mode}")

    es = AsyncElasticsearch(ES_URL)
    try:
        for ward in WARDS:
            dataset = generate_scenario_data(
                scenario=args.scenario,
                hospital_id=HOSPITAL_ID,
                unit_id=ward.unit_id,
                total_beds=ward.total_beds,
                days=args.days,
                seed=args.seed,
            )
            docs = []
            for snap in dataset:
                s = snap.model_dump()
                s["timestamp"] = _shift_timestamp(s["timestamp"], delta)
                docs.append(s)

            span_min = min(d["timestamp"] for d in docs)
            span_max = max(d["timestamp"] for d in docs)

            # Check for any id collisions with existing docs (safety).
            ids = [_doc_id(d) for d in docs]
            existing = await es.mget(index="hospital-snapshots", body={"ids": ids})
            collisions = sum(1 for it in existing["docs"] if it.get("found"))

            print(f"    {ward.unit_id:<14} would add {len(docs):>4} docs  "
                  f"span=[{span_min[:16]} .. {span_max[:16]}]  collisions={collisions}")

            if args.commit:
                if collisions:
                    print(f"      [!] {collisions} id collisions — SKIPPING commit for "
                          f"{ward.unit_id} to avoid overwrite. Adjust --shift-days.")
                    continue
                actions = [
                    {"_index": "hospital-snapshots", "_id": _doc_id(d), "_source": d}
                    for d in docs
                ]
                success, _ = await async_bulk(es, actions, refresh=True)
                print(f"      [OK] inserted {success} new docs for {ward.unit_id}")

        if not args.commit:
            print("\n[DRY-RUN] Nothing written. Re-run with --commit to apply.")
        else:
            print("\n[SUCCESS] Backfill complete (append-only, no overwrites).")
    finally:
        await es.close()


if __name__ == "__main__":
    asyncio.run(main())
