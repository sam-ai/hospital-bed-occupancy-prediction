"""Strategy-loop services: what-if scenarios, accuracy scoring, backtests.

Shared by the REST endpoints (timeline_router) and the Temporal accuracy
workflow. All inference reuses TimesFMHospitalPredictor (lazy singleton).
"""

import asyncio
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.data.elasticsearch_client import (
    fetch_snapshots_from_elasticsearch,
    save_features_to_elasticsearch,
)
from app.data.mock_mcp import HospitalMCPClient
from app.forecasting.feature_pipeline import TimesFMFeaturePipeline
from app.forecasting.model import TimesFMHospitalPredictor
from app.forecasting.scenario import apply_scenario_to_features, summarize_scenario

mcp_hospital = HospitalMCPClient()
feature_pipeline_168 = TimesFMFeaturePipeline(
    context_window_hours=48, forecast_horizon_hours=168
)
_predictor_singleton: TimesFMHospitalPredictor | None = None
_predictor_lock = asyncio.Lock()

CONTEXT_WINDOW_HOURS = 48


async def get_predictor() -> TimesFMHospitalPredictor:
    """Lazily initialize and return the TimesFM predictor singleton."""
    global _predictor_singleton
    if _predictor_singleton is None:
        async with _predictor_lock:
            if _predictor_singleton is None:
                _predictor_singleton = TimesFMHospitalPredictor()
    return _predictor_singleton


async def load_features(hospital_id: str, unit_id: str, persist: bool = False) -> dict[str, Any]:
    """Fetches snapshot history and extracts the shared 168h feature set."""
    snapshots = await fetch_snapshots_from_elasticsearch(
        hospital_id, unit_id, limit=CONTEXT_WINDOW_HOURS
    )
    if len(snapshots) < CONTEXT_WINDOW_HOURS:
        snap = await mcp_hospital.get_complete_snapshot(hospital_id, unit_id)
        fallback = snap.model_dump()
        snapshots = [fallback] * CONTEXT_WINDOW_HOURS

    features = feature_pipeline_168.extract_features(snapshots)
    if persist:
        try:
            await save_features_to_elasticsearch(hospital_id, unit_id, features)
        except Exception as e:
            print(f"[!] Feature persistence skipped ({e})")
    return features


async def run_curve(features: dict[str, Any], horizon: int = 168) -> list[dict[str, Any]]:
    """Runs one TimesFM inference and returns hourly point dicts."""
    predictor = await get_predictor()
    res = predictor.forecast(
        past_target=features["past_target"],
        past_covariates=features["past_covariates"],
        future_covariates=features["future_covariates"],
        total_beds=int(features["static_covariates"][0]),
        horizon_hours=horizon,
    )
    return [p.model_dump() for p in res.points]


# ============================================================================
# WHAT-IF SCENARIO
# ============================================================================
async def run_what_if_scenario(
    hospital_id: str,
    unit_id: str,
    bed_delta: int = 0,
    elective_deferral_pct: float = 0.0,
    er_surge_pct: float = 0.0,
    horizon: int = 168,
) -> dict[str, Any]:
    """Runs baseline + scenario TimesFM forecasts on identical features."""
    features = await load_features(hospital_id, unit_id)

    baseline_points = run_curve(features, horizon)

    scenario_features = apply_scenario_to_features(
        features,
        bed_delta=bed_delta,
        elective_deferral_pct=elective_deferral_pct,
        er_surge_pct=er_surge_pct,
    )
    scenario_points = run_curve(scenario_features, horizon)

    # Freed-bed equivalents always computed against the ORIGINAL capacity so
    # bed_delta changes don't distort the comparison (rates are what changed).
    total_beds_orig = max(1, int(float(features["static_covariates"][0])))
    scen_beds = [round(p["predicted_occupancy"] * total_beds_orig) for p in scenario_points]
    base_beds = [round(p["predicted_occupancy"] * total_beds_orig) for p in baseline_points]

    summary = summarize_scenario(baseline_points[:horizon], scenario_points[:horizon])
    summary["total_beds_after_change"] = max(1, total_beds_orig + int(bed_delta))
    summary["beds_freed_avg"] = round(
        float(np.mean(np.array(base_beds[:horizon]) - np.array(scen_beds[:horizon]))), 2
    )
    diffs = [b - s for b, s in zip(base_beds[:horizon], scen_beds[:horizon])]
    summary["max_hourly_shift"] = int(max(diffs, key=abs)) if diffs else 0

    # Elective OR-window delta: mean % change during weekday 07:00–11:00 hours,
    # where deferral decisions bite hardest.
    now_ref = datetime.now(timezone.utc)
    or_deltas = []
    for i, p in enumerate(scenario_points[:horizon]):
        ts = now_ref + timedelta(hours=i + 1)
        if ts.weekday() < 5 and 7 <= ts.hour <= 11:
            or_deltas.append(
                (baseline_points[i]["predicted_occupancy"] - p["predicted_occupancy"]) * 100
            )
    summary["or_window_delta"] = round(float(np.mean(or_deltas)), 2) if or_deltas else 0.0

    now = datetime.now(timezone.utc)
    timestamps = [(now + timedelta(hours=i + 1)).isoformat() for i in range(horizon)]

    return {
        "hospital_id": hospital_id,
        "unit_id": unit_id,
        "params": {
            "bed_delta": bed_delta,
            "elective_deferral_pct": elective_deferral_pct,
            "er_surge_pct": er_surge_pct,
        },
        "summary": summary,
        "baseline": [
            {
                "timestamp": ts,
                "predicted_occupancy": round(p["predicted_occupancy"], 4),
                "lower_bound": p["lower_bound"],
                "upper_bound": p["upper_bound"],
            }
            for ts, p in zip(timestamps, baseline_points)
        ],
        "scenario": [
            {
                "timestamp": ts,
                "predicted_occupancy": round(p["predicted_occupancy"], 4),
                "lower_bound": p["lower_bound"],
                "upper_bound": p["upper_bound"],
            }
            for ts, p in zip(timestamps, scenario_points)
        ],
    }


# ============================================================================
# ACCURACY SCORING (A1) + BACKTEST (A3)
# ============================================================================
def score_forecast_vs_actuals(
    forecast_points: list[dict],
    actuals_by_hour: dict[int, float],
) -> dict[str, Any] | None:
    """Scores forecast points against actual occupancy rates keyed by hour offset.

    actuals_by_hour: {1: 0.62, 2: 0.65, ...} — hour offset -> occupancy rate.
    Returns None when there is no overlap.
    """
    errors: list[float] = []
    signed: list[float] = []
    actual_values: list[float] = []
    for i, point in enumerate(forecast_points):
        hour = i + 1
        if hour not in actuals_by_hour:
            continue
        pred = float(point["predicted_occupancy"])
        actual = actuals_by_hour[hour]
        signed.append(pred - actual)
        errors.append(abs(pred - actual))
        actual_values.append(actual)

    if not errors:
        return None

    mae = float(np.mean(errors))
    bias = float(np.mean(signed))
    rmse = float(np.sqrt(np.mean(np.array(errors) ** 2)))

    # MAPE — skip near-zero actuals to avoid division explosions
    pct_errors = [
        abs(s) / a for s, a in zip(signed, actual_values) if a >= 0.05
    ]
    mape = round(float(np.mean(pct_errors)) * 100, 2) if pct_errors else None

    return {
        "mae": round(mae * 100, 2),          # % points
        "rmse": round(rmse * 100, 2),         # % points
        "mape": mape,                         # % relative
        "bias": round(bias * 100, 2),         # signed, % points
        "points_evaluated": len(errors),
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
    }


def build_actuals_from_snapshots(
    snapshots: list[dict],
    start_after: datetime,
    hours: int,
) -> dict[int, float]:
    """Builds {hour_offset: occupancy_rate} from chronologically ordered snapshots.

    Snapshots at/after start_after are matched to consecutive hour offsets.
    """
    total_beds_cache: dict[str, int] = {}
    actuals: dict[int, float] = {}
    hour = 1
    for s in snapshots:
        try:
            ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        if ts < start_after:
            continue
        if hour > hours:
            break
        census = s.get("census", {})
        hosp = s.get("hospital_id", "?")
        beds = total_beds_cache.setdefault(
            hosp, int(census.get("total_beds", 10)) or 10
        )
        occupied = census.get("occupied_beds", 0)
        actuals[hour] = occupied / max(beds, 1)
        hour += 1
    return actuals


async def run_backtest(
    hospital_id: str,
    unit_id: str,
    days: int = 14,
    persist_curves: bool = False,
) -> dict[str, Any]:
    """Walks history scoring next-day 24H forecasts against actual outcomes.

    For day D (walking backwards from the newest snapshots): context window =
    the 48h of snapshots preceding D's first predicted hour; ground truth =
    that day's actual occupancy. Requires ~days*24 + 48 snapshots of history.
    """
    snapshots = await fetch_snapshots_from_elasticsearch(
        hospital_id, unit_id, limit=min(2000, days * 24 + CONTEXT_WINDOW_HOURS + 24)
    )
    if len(snapshots) < CONTEXT_WINDOW_HOURS + 24:
        snap = await mcp_hospital.get_complete_snapshot(hospital_id, unit_id)
        fallback = snap.model_dump()
        snapshots = [fallback] * (CONTEXT_WINDOW_HOURS + 48)

    n = len(snapshots)
    per_day = []
    predictor = get_predictor()

    # Model comparison accumulators: TimesFM vs statistical vs naive persistence
    models = {
        "timesfm": {"errors": [], "signed": [], "actuals": []},
        "naive_persistence": {"errors": [], "signed": [], "actuals": []},
    }

    # Walk forward through contiguous 24h blocks after the first context window
    max_blocks = min(days, max(1, (n - CONTEXT_WINDOW_HOURS) // 24))
    for block in range(max_blocks):
        ctx_end = CONTEXT_WINDOW_HOURS + block * 24
        truth_start = ctx_end
        truth_end = min(ctx_end + 24, n)
        if truth_end - truth_start < 12:
            break

        window = snapshots[ctx_end - CONTEXT_WINDOW_HOURS : ctx_end]
        try:
            features = feature_pipeline_168.extract_features(window)
        except Exception:
            continue

        forecast = predictor.forecast(
            past_target=features["past_target"],
            past_covariates=features["past_covariates"],
            future_covariates=features["future_covariates"],
            total_beds=int(window[-1]["census"]["total_beds"]),
            horizon_hours=24,
        )
        points = [p.model_dump() for p in forecast.points]

        actuals = build_actuals_from_snapshots(snapshots[truth_start:truth_end], start_after=datetime.min.replace(tzinfo=timezone.utc), hours=24)
        scored = score_forecast_vs_actuals(points, actuals)
        if scored:
            day_ts = snapshots[truth_start]["timestamp"]
            per_day.append({"day": day_ts, **scored})

        # ---- Optionally persist the full historical curve as a back-dated
        # forecast so the UI can explore it (honest: context ends at day start)
        if persist_curves:
            await _persist_backtest_curve(
                hospital_id, unit_id, snapshots, truth_start, truth_end, points
            )

        # ---- Comparison baselines on the same truth window ----
        last_occ = float(window[-1]["census"]["occupied_beds"]) / max(
            int(window[-1]["census"]["total_beds"]), 1
        )
        for hour, actual in actuals.items():
            if hour > len(points):
                continue
            tm_pred = float(points[hour - 1]["predicted_occupancy"])
            models["timesfm"]["errors"].append(abs(tm_pred - actual))
            models["timesfm"]["signed"].append(tm_pred - actual)
            models["timesfm"]["actuals"].append(actual)

            nv_pred = last_occ  # naive: occupancy stays flat
            models["naive_persistence"]["errors"].append(abs(nv_pred - actual))
            models["naive_persistence"]["signed"].append(nv_pred - actual)
            models["naive_persistence"]["actuals"].append(actual)

    aggregate = None
    if per_day:
        maes = [d["mae"] for d in per_day]
        biases = [d["bias"] for d in per_day]
        aggregate = {
            "days_evaluated": len(per_day),
            "mae_avg": round(float(np.mean(maes)), 2),
            "mae_max": round(float(np.max(maes)), 2),
            "bias_avg": round(float(np.mean(biases)), 2),
        }

    # ---- Model comparison summary ----
    comparison: list[dict[str, Any]] = []
    for name, acc in models.items():
        if not acc["errors"]:
            continue
        mae = float(np.mean(acc["errors"]))
        rmse = float(np.sqrt(np.mean(np.array(acc["errors"]) ** 2)))
        comparison.append(
            {
                "model": name,
                "mae": round(mae * 100, 2),
                "rmse": round(rmse * 100, 2),
                "bias": round(float(np.mean(acc["signed"])) * 100, 2),
            }
        )
    comparison.sort(key=lambda m: m["mae"])

    return {
        "hospital_id": hospital_id,
        "unit_id": unit_id,
        "aggregate": aggregate,
        "per_day": per_day,
        "models": comparison,
    }


async def _persist_backtest_curve(
    hospital_id: str,
    unit_id: str,
    snapshots: list[dict],
    truth_start: int,
    truth_end: int,
    points: list[dict],
) -> None:
    """Stores one past-day TimesFM curve under that day's forecast_date.

    Point timestamps are rewritten to the actual day they predict so the
    multi-horizon endpoint can serve them for back-dated views.
    """
    from app.data.elasticsearch_client import FORECAST_INDEX, es_client

    if truth_start >= len(snapshots):
        return
    day_snap = snapshots[truth_start]
    try:
        day_dt = datetime.fromisoformat(
            day_snap["timestamp"].replace("Z", "+00:00")
        )
    except Exception:
        return
    forecast_date = day_dt.strftime("%Y-%m-%d")

    docs = []
    for i, p in enumerate(points):
        pt_ts = (day_dt + timedelta(hours=i + 1)).isoformat()
        doc_id = f"{forecast_date}_{hospital_id}_{unit_id}_24H_STEP{i + 1:03d}"
        docs.append(
            {
                "_index": FORECAST_INDEX,
                "_id": doc_id,
                "_source": {
                    "forecast_date": forecast_date,
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "hospital_id": hospital_id,
                    "unit_id": unit_id,
                    "horizon_type": "24H",
                    "time_step_index": i + 1,
                    "timestamp": pt_ts,
                    "predicted_occupancy": p["predicted_occupancy"],
                    "predicted_occupied_beds": int(
                        round(p["predicted_occupancy"] * 10)
                    ),
                    "peak_occupancy": p["predicted_occupancy"],
                    "lower_bound": p["lower_bound"],
                    "upper_bound": p["upper_bound"],
                    "has_anomaly": False,
                    "anomaly_severity": "none",
                },
            }
        )

    from elasticsearch.helpers import async_bulk

    await async_bulk(es_client, docs, refresh="wait_for")
