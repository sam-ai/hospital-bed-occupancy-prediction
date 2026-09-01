"""One-off seeder: populate ALL scenarios for all wards.

This script:
  1. Iterates over all five scenarios (balanced, high_capacity, volatile,
     recovery, outbreak_surge).
  2. Generates `days` of hourly snapshots for EACH ward under each scenario.
  3. Writes a separate JSON file per scenario into `data/`.
  4. Bulk-ingests each scenario's data into the `hospital-snapshots` index
     on Elasticsearch.  Each document carries a `scenario` field so it can be
     queried independently of other scenarios.
  5. Optionally triggers Temporal forecast workflows (disabled by default to
     keep scenarios independent).

Usage:
    uv run python scripts/seed_all_scenarios.py --days 30
    uv run python scripts/seed_all_scenarios.py --days 14 --seed 7
    uv run python scripts/seed_all_scenarios.py --days 30 --no-ingest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure project root is importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk

from app.data.mock_regimes import SCENARIOS, generate_scenario_data
from app.data.wards import WARDS

HOSPITAL_ID = "HOSPITAL-MAIN-01"
ES_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
DATA_DIR = Path(__file__).parent.parent / "data"


def _doc_id(s: dict, scenario: str) -> str:
    """Deterministic snapshot doc id that includes the scenario so different
    regimes for the same ward/timestamp do not collide."""
    return (
        f"{s.get('hospital_id', 'UNKNOWN')}_"
        f"{scenario}_"
        f"{s.get('census', {}).get('unit_id', 'UNKNOWN')}_"
        f"{s.get('timestamp', 'NO_TS').replace(':', '-').replace('.', '-')}"
    )


async def ensure_index(es: AsyncElasticsearch) -> None:
    """Ensure the hospital-snapshots index exists with proper mapping."""
    if not await es.indices.exists(index="hospital-snapshots"):
        mapping = {
            "mappings": {
                "properties": {
                    "timestamp": {"type": "date"},
                    "hospital_id": {"type": "keyword"},
                    "scenario": {"type": "keyword"},
                    "census": {
                        "properties": {
                            "unit_id": {"type": "keyword"},
                            "unit_type": {"type": "keyword"},
                            "total_beds": {"type": "integer"},
                            "occupied_beds": {"type": "integer"},
                            "blocked_beds": {"type": "integer"},
                            "admissions_24h": {"type": "integer"},
                            "discharges_24h": {"type": "integer"},
                            "pending_discharges_today": {"type": "integer"},
                            "staff_on_duty": {"type": "integer"},
                            "average_los_hours": {"type": "float"},
                            "bed_turnover_time_hours": {"type": "float"},
                        }
                    },
                    "er_arrivals": {
                        "properties": {
                            "er_current_waiting_count": {"type": "integer"},
                            "er_admit_decisions_pending": {"type": "integer"},
                            "er_high_acuity_arrivals_last_6h": {"type": "integer"},
                        }
                    },
                    "scheduled_cases": {
                        "properties": {
                            "scheduled_elective_admissions_24h": {"type": "integer"},
                            "scheduled_post_op_icu_beds": {"type": "integer"},
                            "same_day_surgeries_count": {"type": "integer"},
                        }
                    },
                    "external_signals": {
                        "type": "nested",
                        "properties": {
                            "signal_type": {"type": "keyword"},
                            "value": {"type": "float"},
                            "direction": {"type": "keyword"},
                            "severity": {"type": "keyword"},
                            "confidence": {"type": "float"},
                        },
                    },
                    "historical_occupancy_48h": {"type": "integer"},
                }
            }
        }
        await es.indices.create(index="hospital-snapshots", body=mapping)
        print(f"[✓] Created index 'hospital-snapshots' with mapping")
    else:
        print(f"[*] Index 'hospital-snapshots' already exists")


async def seed_scenario(
    es: AsyncElasticsearch,
    scenario: str,
    days: int,
    seed: int | None,
) -> tuple[list, dict[str, int]]:
    """Generate data for a single scenario across all wards.

    Returns a tuple of (dataset, counts_by_ward).
    """
    counts: dict[str, int] = {}
    dataset = []

    print(f"\n[*] Generating scenario: {scenario}")
    print(f"    Description: {SCENARIOS[scenario].description}")

    for ward in WARDS:
        ward_data = generate_scenario_data(
            scenario=scenario,
            hospital_id=HOSPITAL_ID,
            unit_id=ward.unit_id,
            total_beds=ward.total_beds,
            days=days,
            seed=seed,
        )
        dataset.extend(ward_data)

        occ = [s.census.occupied_beds for s in ward_data]
        print(
            f"    [✓] {ward.unit_id:<14} ({ward.unit_type:<9}) "
            f"{len(ward_data)} recs | beds {ward.total_beds} | "
            f"occupancy {min(occ)}-{max(occ)} (mean {sum(occ)/len(occ):.1f})"
        )
        counts[ward.unit_id] = len(ward_data)

    # Write scenario-specific JSON file
    out_file = DATA_DIR / f"hospital_30day_mock_data_{scenario}.json"
    # Add scenario field to each snapshot for queryability
    with open(out_file, "w") as f:
        json.dump(
            [{"scenario": scenario, **s.model_dump()} for s in dataset],
            f,
            indent=2,
        )
    print(f"    [✓] Wrote {len(dataset)} snapshots → {out_file}")

    return dataset, counts


async def ingest_scenario(
    es: AsyncElasticsearch,
    scenario: str,
    dataset: list,
) -> int:
    """Bulk-ingest a scenario's dataset into Elasticsearch.

    Returns the number of successfully ingested documents.
    """
    actions = []
    for s in dataset:
        s_dict = s.model_dump()
        actions.append({
            "_index": "hospital-snapshots",
            "_id": _doc_id(s_dict, scenario),
            "_source": {"scenario": scenario, **s_dict},
        })

    success, errors = await async_bulk(es, actions, refresh=True)
    if errors:
        print(f"    [!] {len(errors)} errors during ingestion:")
        for err in errors[:5]:  # Show first 5 errors
            print(f"        {err}")
    print(f"    [✓] Ingested {success} snapshots into Elasticsearch")
    return success


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed all scenarios for all wards into Elasticsearch."
    )
    parser.add_argument("--days", type=int, default=30, help="Number of days to generate")
    parser.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    parser.add_argument(
        "--no-ingest",
        action="store_true",
        help="Skip Elasticsearch ingestion; only write JSON files",
    )
    args = parser.parse_args()

    print(f"[*] ES={ES_URL}  days={args.days}  seed={args.seed}")
    print(f"[*] Scenarios: {', '.join(SCENARIOS.keys())}")
    print(f"[*] Wards: {', '.join(w.unit_id for w in WARDS)}")

    DATA_DIR.mkdir(exist_ok=True)

    es = AsyncElasticsearch(ES_URL)
    try:
        # Ensure index exists with proper mapping
        await ensure_index(es)

        for scenario in SCENARIOS:
            dataset, counts = await seed_scenario(
                es=es,
                scenario=scenario,
                days=args.days,
                seed=args.seed,
            )

            if not args.no_ingest:
                await ingest_scenario(es, scenario, dataset)

        # Verify ingestion
        if not args.no_ingest:
            print("\n=== Verification: snapshots per scenario ===")
            resp = await es.search(
                index="hospital-snapshots",
                body={
                    "size": 0,
                    "aggs": {"by_scenario": {"terms": {"field": "scenario", "size": 10}}},
                },
            )
            for bucket in resp["aggregations"]["by_scenario"]["buckets"]:
                print(f"    {bucket['key']:<20} {bucket['doc_count']}")

            print("\n[SUCCESS] All scenarios seeded.")
        else:
            print("\n[OK] JSON files written (no ingest requested)")
    finally:
        await es.close()


if __name__ == "__main__":
    asyncio.run(main())
