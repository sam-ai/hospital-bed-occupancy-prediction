"""Nightly forecast-accuracy scoring workflow (strategy loop: Learn).

Compares today's 24H TimesFM forecast points against actual snapshot
occupancy recorded during the day, persists per-day MAE/bias to
'hospital-forecast-accuracy', and powers the UI trust badge.

Cron: '50 23 * * *' (23:50 daily).
"""

from datetime import datetime, timedelta, timezone

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    import numpy as np

    from app.data.elasticsearch_client import (
        es_client,
        save_accuracy_record,
    )
    from app.models import ForecastPoint, ForecastResult


@activity.defn
async def run_forecast_accuracy_activity(
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
) -> dict:
    """Scores today's 24H forecast against today's actual snapshots."""
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    # ---- Fetch today's 24H forecast points ----
    response = await es_client.search(
        index="hospital-forecast-timeline",
        body={
            "size": 24,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"hospital_id": hospital_id}},
                        {"term": {"unit_id": unit_id}},
                        {"term": {"forecast_date": today_str}},
                        {"term": {"horizon_type": "24H"}},
                    ]
                }
            },
            "sort": [{"time_step_index": {"order": "asc"}}],
        },
    )
    forecast_points = [hit["_source"] for hit in response["hits"]["hits"]]
    if not forecast_points:
        return {"status": "NO_FORECAST", "scored": False}

    # ---- Fetch today's actual snapshots ----
    day_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    snap_response = await es_client.search(
        index="hospital-snapshots",
        body={
            "size": 200,
            "query": {
                "bool": {
                    "must": [
                        {"term": {"hospital_id": hospital_id}},
                        {"term": {"census.unit_id": unit_id}},
                        {"range": {"timestamp": {"gte": day_start.isoformat()}}},
                    ]
                }
            },
            "sort": [{"timestamp": {"order": "asc"}}],
        },
    )
    actual_snaps = [hit["_source"] for hit in snap_response["hits"]["hits"]]

    # Parse and index actuals by wall-clock hour
    actuals_by_hour: dict[int, float] = {}
    for s in actual_snaps:
        try:
            ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        census = s.get("census", {})
        beds = max(int(census.get("total_beds", 10)), 1)
        actuals_by_hour[ts.hour] = census.get("occupied_beds", 0) / beds

    # ---- Score: match each forecast point's UTC hour to actual ----
    errors, signed, actual_values = [], [], []
    for fp in forecast_points:
        try:
            ft = datetime.fromisoformat(fp["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        hour = ft.hour
        # nearest actual within 90 minutes
        best = None
        for offset in (0, -1, 1, 2):
            if hour + offset in actuals_by_hour:
                best = actuals_by_hour[hour + offset]
                break
        if best is None:
            continue
        pred = float(fp["predicted_occupancy"])
        errors.append(abs(pred - best))
        signed.append(pred - best)
        actual_values.append(best)

    if not errors:
        return {"status": "NO_ACTUALS", "scored": False}

    record = {
        "day": today_str,
        "horizon_type": "24H",
        "hospital_id": hospital_id,
        "unit_id": unit_id,
        "mae": round(float(np.mean(errors)) * 100, 2),
        "rmse": round(float(np.sqrt(np.mean(np.array(errors) ** 2))) * 100, 2),
        "mape": (
            round(
                float(
                    np.mean([
                        abs(sv) / av
                        for sv, av in zip(signed, actual_values)
                        if av >= 0.05
                    ])
                )
                * 100,
                2,
            )
            if any(av >= 0.05 for av in actual_values)
            else None
        ),
        "bias": round(float(np.mean(signed)) * 100, 2),
        "points_evaluated": len(errors),
        "evaluated_at": now.isoformat(),
    }
    await save_accuracy_record(record)
    return {"status": "SUCCESS", "scored": True, **record}


@workflow.defn
class ForecastAccuracyWorkflow:
    """Cron schedule: '50 23 * * *' — nightly forecast vs actual scoring."""

    @workflow.run
    async def run(self, hospital_id: str = "HOSPITAL-MAIN-01", unit_id: str = "FLOOR-1") -> dict:
        return await workflow.execute_activity(
            run_forecast_accuracy_activity,
            args=[hospital_id, unit_id],
            start_to_close_timeout=timedelta(minutes=5),
        )
