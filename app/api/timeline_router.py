"""FastAPI router exposing multi-horizon forecast data for UI visualization.

Frontend queries:
    GET  /api/forecast/multi-horizon?horizon_type=24H   (hourly, 24 points)
    GET  /api/forecast/multi-horizon?horizon_type=7D    (daily, 7 points)
    GET  /api/forecast/multi-horizon?horizon_type=6M    (monthly, 6 points)
    POST /api/forecast/scenario                          (what-if simulator)
    POST /api/forecast/backtest                          (historical accuracy)
    GET  /api/forecast/accuracy?horizon_type=24H&days=7  (trust badge)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.data.elasticsearch_client import (
    FORECAST_INDEX,
    fetch_accuracy_records,
    save_accuracy_record,
    es_client,
)

router = APIRouter(prefix="/api/forecast", tags=["Multi-Horizon Forecasts"])


@router.get("/multi-horizon")
async def get_multi_horizon_forecast(
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
    horizon_type: str = Query("24H", enum=["24H", "7D", "6M"]),
    date: str | None = Query(None, description="Exact forecast_date (YYYY-MM-DD) for back-dated views"),
) -> dict:
    """Fetches forecast points for the requested horizon from Elasticsearch.

    Prefers today's forecasts; falls back to the most recent available
    forecast_date. A `date` param pins an exact (possibly past) forecast day
    and joins actual occupancy for predicted-vs-actual comparison.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pinned_past = bool(date and date < today_str)

    base_filters = [
        {"term": {"hospital_id": hospital_id}},
        {"term": {"unit_id": unit_id}},
        {"term": {"horizon_type": horizon_type}},
    ]

    async def _search(forecast_date: str | None) -> list[dict]:
        must = list(base_filters)
        if forecast_date:
            must.append({"term": {"forecast_date": forecast_date}})
        response = await es_client.search(
            index=FORECAST_INDEX,
            body={
                "size": 200,
                "query": {"bool": {"must": must}},
                "sort": [{"time_step_index": {"order": "asc"}}],
            },
        )
        return [hit["_source"] for hit in response["hits"]["hits"]]

    try:
        if date:
            # Pinned view: exact date only, no fallback
            points = await _search(date)
            effective_date = date
        else:
            points = await _search(today_str)
            effective_date = today_str
            if not points:
                points = await _search(None)
                if points:
                    effective_date = points[0].get("forecast_date", today_str)

        is_past = effective_date < today_str

        # ---- Actuals overlay for past dates ----
        actuals: dict[int, float] | None = None
        if is_past and points:
            actuals = await _fetch_actuals_for_date(
                hospital_id, unit_id, effective_date
            )
            for i, p in enumerate(points):
                hour = i + 1
                if actuals and hour in actuals:
                    p["actual_occupancy"] = round(actuals[hour], 4)

    except Exception as e:
        return {
            "forecast_date": date or today_str,
            "horizon_type": horizon_type,
            "total_points": 0,
            "error": f"Elasticsearch query failed: {e}",
            "points": [],
        }

    return {
        "forecast_date": effective_date,
        "horizon_type": horizon_type,
        "is_past": is_past,
        "total_points": len(points),
        "points": points,
    }


async def _fetch_actuals_for_date(
    hospital_id: str, unit_id: str, forecast_date: str
) -> dict[int, float]:
    """Hour-offset -> actual occupancy rate for the given calendar day."""
    try:
        response = await es_client.search(
            index="hospital-snapshots",
            body={
                "size": 100,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"hospital_id": hospital_id}},
                            {"term": {"census.unit_id": unit_id}},
                            {"range": {"timestamp": {"gte": f"{forecast_date}||+0d", "lt": f"{forecast_date}||+1d"}}},
                        ]
                    }
                },
                "sort": [{"timestamp": {"order": "asc"}}],
            },
        )
    except Exception:
        return {}

    actuals: dict[int, float] = {}
    for i, hit in enumerate(response["hits"]["hits"], start=1):
        census = hit["_source"].get("census", {})
        beds = max(int(census.get("total_beds", 10)), 1)
        actuals[i] = census.get("occupied_beds", 0) / beds
    return actuals


@router.get("/history-dates")
async def get_forecast_history_dates(
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
    horizon_type: str = Query("24H", enum=["24H", "7D", "6M"]),
    limit: int = Query(14, ge=1, le=60),
) -> dict:
    """Distinct forecast dates available for back-dated exploration."""
    try:
        response = await es_client.search(
            index=FORECAST_INDEX,
            body={
                "size": 0,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"hospital_id": hospital_id}},
                            {"term": {"unit_id": unit_id}},
                            {"term": {"horizon_type": horizon_type}},
                        ]
                    }
                },
                "aggs": {
                    "dates": {
                        "terms": {
                            "field": "forecast_date",
                            "size": limit,
                            "order": {"_key": "desc"},
                        }
                    }
                },
            },
        )
        dates = [
            b["key_as_string"][:10]
            for b in response["aggregations"]["dates"]["buckets"]
        ]
    except Exception as e:
        return {"dates": [], "error": str(e)}

    return {"dates": dates}


# ============================================================================
# WHAT-IF SCENARIO SIMULATOR (strategy planning)
# ============================================================================
class ScenarioRequest(BaseModel):
    hospital_id: str = "HOSPITAL-MAIN-01"
    unit_id: str = "FLOOR-1"
    bed_delta: int = Query(0, ge=-5, le=5)
    elective_deferral_pct: float = Query(0.0, ge=0, le=100)
    er_surge_pct: float = Query(0.0, ge=-50, le=100)


@router.post("/scenario")
async def run_scenario(req: ScenarioRequest) -> dict:
    """Runs baseline vs what-if scenario TimesFM forecasts on identical features."""
    from app.forecasting.strategy_service import run_what_if_scenario

    try:
        return await run_what_if_scenario(
            hospital_id=req.hospital_id,
            unit_id=req.unit_id,
            bed_delta=req.bed_delta,
            elective_deferral_pct=req.elective_deferral_pct,
            er_surge_pct=req.er_surge_pct,
        )
    except Exception as e:
        return {"error": f"Scenario failed: {e}"}


# ============================================================================
# BACKTEST REPLAY (historical forecast accuracy)
# ============================================================================
class BacktestRequest(BaseModel):
    hospital_id: str = "HOSPITAL-MAIN-01"
    unit_id: str = "FLOOR-1"
    days: int = Query(14, ge=1, le=30)
    persist: bool = True
    persist_curves: bool = False


@router.post("/backtest")
async def run_backtest_endpoint(req: BacktestRequest) -> dict:
    """Walks history scoring next-day forecasts vs actuals (TimesFM)."""
    from app.forecasting.strategy_service import run_backtest

    result = await run_backtest(
        hospital_id=req.hospital_id,
        unit_id=req.unit_id,
        days=req.days,
        persist_curves=req.persist_curves,
    )

    if req.persist and result.get("per_day"):
        for day_record in result["per_day"]:
            await save_accuracy_record({
                **day_record,
                "horizon_type": "24H",
                "hospital_id": req.hospital_id,
                "unit_id": req.unit_id,
            })

    return result


# ============================================================================
# ACCURACY TRUST BADGE
# ============================================================================
@router.get("/accuracy")
async def get_forecast_accuracy(
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
    horizon_type: str = Query("24H", enum=["24H", "7D", "6M"]),
    days: int = Query(7, ge=1, le=30),
) -> dict:
    """Returns recent daily forecast accuracy records + aggregate trust badge."""
    records = await fetch_accuracy_records(hospital_id, unit_id, horizon_type, days)

    aggregate = None
    if records:
        maes = [r["mae"] for r in records]
        biases = [r["bias"] for r in records]
        mae_avg = sum(maes) / len(maes)
        aggregate = {
            "days_evaluated": len(records),
            "mae_avg": round(mae_avg, 2),
            "bias_avg": round(sum(biases) / len(biases), 2),
            # Trust grade: green < 3% MAE, amber < 6%, red otherwise
            "grade": "good" if mae_avg < 3 else ("fair" if mae_avg < 6 else "poor"),
        }

    return {"horizon_type": horizon_type, "aggregate": aggregate, "records": records}


# ============================================================================
# PATIENT-FLOW FORECAST (daily admissions & discharges)
# ============================================================================
@router.get("/patient-flow")
async def get_patient_flow_forecast(
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
    days: int = Query(7, ge=1, le=14),
) -> dict:
    """7-day admissions vs discharges forecast derived from occupancy history."""
    from app.data.elasticsearch_client import fetch_snapshots_from_elasticsearch
    from app.forecasting.flow_service import run_patient_flow_forecast

    snapshots = await fetch_snapshots_from_elasticsearch(
        hospital_id, unit_id, limit=800
    )
    return await run_patient_flow_forecast(
        snapshots, hospital_id, unit_id, days=days
    )
