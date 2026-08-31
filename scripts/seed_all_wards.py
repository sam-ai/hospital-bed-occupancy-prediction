"""One-off seeder: populate ALL wards + purge stale legacy docs + forecast.

Option A helper (no app code changes). This script:
  1. Deletes stale legacy docs (default: unit_id == "FLOOR-1") from every index.
  2. Generates `days` of hourly snapshots for EACH ward in the registry,
     with the ward's own unit_id / total_beds, and bulk-ingests them into
     `hospital-snapshots` (deterministic doc IDs mirror the gateway/ingest
     convention so re-runs overwrite cleanly).
  3. Triggers a DailyForecastWorkflow per ward via Temporal and waits for
     each result so the 24H forecast-timeline is populated for every ward.

Usage:
    uv run python scripts/seed_all_wards.py --scenario balanced --days 30
    uv run python scripts/seed_all_wards.py --scenario outbreak_surge --no-forecast
"""

from __future__ import annotations

import argparse
import asyncio
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
TEMPORAL_HOST = os.getenv("TEMPORAL_HOST", "localhost:7233")
TEMPORAL_NAMESPACE = os.getenv("TEMPORAL_NAMESPACE", "default")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "hospital-ai-queue")

# Indices that carry a per-record ward id we may need to clean stale docs from.
STALE_INDICES = {
    "hospital-snapshots": "census.unit_id",
    "hospital-features": "unit_id",
    "hospital-forecast-timeline": "unit_id",
    "hospital-forecast-accuracy": "unit_id",
    "hospital-patient-flow": "unit_id",
}


def _doc_id(s: dict) -> str:
    """Deterministic snapshot doc id (mirrors gateway.regenerate_mock_regime)."""
    return (
        f"{s.get('hospital_id', 'UNKNOWN')}_"
        f"{s.get('census', {}).get('unit_id', 'UNKNOWN')}_"
        f"{s.get('timestamp', 'NO_TS').replace(':', '-').replace('.', '-')}"
    )


async def purge_stale(es: AsyncElasticsearch, stale_units: list[str]) -> None:
    print(f"[*] Purging stale ward docs {stale_units} from all indices...")
    for index, field in STALE_INDICES.items():
        if not await es.indices.exists(index=index):
            print(f"    - {index}: (missing, skipped)")
            continue
        try:
            resp = await es.delete_by_query(
                index=index,
                body={"query": {"terms": {field: stale_units}}},
                conflicts="proceed",
                refresh=True,
            )
            print(f"    - {index}: deleted {resp.get('deleted', 0)}")
        except Exception as e:  # noqa: BLE001
            print(f"    - {index}: purge failed ({e})")


async def seed_wards(es: AsyncElasticsearch, scenario: str, days: int, seed: int | None) -> dict[str, int]:
    counts: dict[str, int] = {}
    for ward in WARDS:
        dataset = generate_scenario_data(
            scenario=scenario,
            hospital_id=HOSPITAL_ID,
            unit_id=ward.unit_id,
            total_beds=ward.total_beds,
            days=days,
            seed=seed,
        )
        actions = [
            {"_index": "hospital-snapshots", "_id": _doc_id(s), "_source": s}
            for s in (snap.model_dump() for snap in dataset)
        ]
        success, _ = await async_bulk(es, actions, refresh=True)
        counts[ward.unit_id] = success
        print(f"[âœ“] {ward.unit_id:<14} ingested {success} snapshots "
              f"(beds={ward.total_beds}, type={ward.unit_type})")
    return counts


async def trigger_forecasts(horizons: list[str]) -> dict[str, str]:
    from temporalio.client import Client

    from app.temporal.scheduled_workflow import (
        DailyForecastWorkflow,
        MonthlyForecastWorkflow,
        WeeklyForecastWorkflow,
    )

    # horizon key -> (workflow class, human label)
    global _HORIZON_WORKFLOWS
    _HORIZON_WORKFLOWS = {
        "24H": (DailyForecastWorkflow, "Daily"),
        "7D": (WeeklyForecastWorkflow, "Weekly"),
        "6M": (MonthlyForecastWorkflow, "Monthly"),
    }

    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)
    results: dict[str, str] = {}
    for horizon in horizons:
        wf_cls, tag = _HORIZON_WORKFLOWS[horizon]
        print(f"\n[*] Triggering {tag} ({horizon}) forecasts for all wards...")
        for ward in WARDS:
            wf_id = f"seed-{horizon}-{ward.unit_id}"
            handle = await client.start_workflow(
                wf_cls.run,
                args=[HOSPITAL_ID, ward.unit_id],
                id=wf_id,
                task_queue=TEMPORAL_TASK_QUEUE,
            )
            res = await handle.result()
            results[f"{ward.unit_id}:{horizon}"] = res
            print(f"[OK] {ward.unit_id:<14} {horizon:<7} -> {res}")
    return results


async def verify(es: AsyncElasticsearch) -> None:
    print("\n=== Verification: snapshots per ward ===")
    resp = await es.search(
        index="hospital-snapshots",
        body={"size": 0, "aggs": {"w": {"terms": {"field": "census.unit_id", "size": 20}}}},
    )
    for b in resp["aggregations"]["w"]["buckets"]:
        print(f"    {b['key']:<14} {b['doc_count']}")

    for horizon in ("24H", "7D", "6M"):
        print(f"=== Verification: forecast-timeline {horizon} per ward ===")
        resp = await es.search(
            index="hospital-forecast-timeline",
            body={
                "size": 0,
                "query": {"term": {"horizon_type": horizon}},
                "aggs": {"w": {"terms": {"field": "unit_id", "size": 20}}},
            },
        )
        buckets = resp["aggregations"]["w"]["buckets"]
        if not buckets:
            print("    (none)")
        for b in buckets:
            print(f"    {b['key']:<14} {b['doc_count']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Seed all wards with mock data.")
    parser.add_argument("--scenario", default="balanced", choices=list(SCENARIOS))
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--no-forecast", action="store_true",
                        help="Skip Temporal forecast triggering.")
    parser.add_argument("--no-seed", action="store_true",
                        help="Skip snapshot generation/ingest (only trigger forecasts).")
    parser.add_argument("--horizons", nargs="+", default=["24H", "7D", "6M"],
                        choices=["24H", "7D", "6M"],
                        help="Forecast horizons to trigger per ward.")
    parser.add_argument("--stale-units", nargs="*", default=["FLOOR-1"],
                        help="Legacy ward ids to purge from all indices.")
    args = parser.parse_args()

    print(f"[*] ES={ES_URL}  scenario={args.scenario}  days={args.days}  "
          f"horizons={args.horizons}")
    es = AsyncElasticsearch(ES_URL)
    try:
        if args.stale_units:
            await purge_stale(es, args.stale_units)
        if not args.no_seed:
            await seed_wards(es, args.scenario, args.days, args.seed)
        else:
            print("[*] --no-seed set; skipping snapshot generation.")

        if not args.no_forecast:
            await trigger_forecasts(args.horizons)
        else:
            print("\n[*] --no-forecast set; skipping forecast triggers.")

        await verify(es)
        print("\n[SUCCESS] All wards seeded.")
    finally:
        await es.close()


if __name__ == "__main__":
    asyncio.run(main())


