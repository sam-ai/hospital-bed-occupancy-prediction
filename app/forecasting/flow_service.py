"""Patient-flow forecasting: daily admissions & discharges (guide §3.1-3.2).

Derives daily admission/discharge count series from historical snapshot
occupancy deltas, then runs TimesFM zero-shot per series for a 7-day
horizon. Series semantics:

    admissions(t) = max(0, occupancy(t) - occupancy(t-1))   # inflow-driven
    discharges(t) = max(0, occupancy(t-1) - occupancy(t))   # outflow-driven

Daily aggregation sums the hourly deltas per calendar day; the resulting
~30-day daily series becomes the forecast context.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.forecasting.strategy_service import get_predictor


def _daily_flows(snapshots: list[dict]) -> tuple[list[float], list[float], list[str]]:
    """Returns (daily_admissions, daily_discharges, day_labels)."""
    occ_by_day: dict[str, list[int]] = {}
    order: list[str] = []

    for s in snapshots:
        census = s.get("census", {})
        try:
            ts = datetime.fromisoformat(s["timestamp"].replace("Z", "+00:00"))
        except Exception:
            continue
        day = ts.strftime("%Y-%m-%d")
        if day not in occ_by_day:
            occ_by_day[day] = []
            order.append(day)
        occ_by_day[day].append(int(census.get("occupied_beds", 0)))

    admissions: list[float] = []
    discharges: list[float] = []
    labels: list[str] = []
    prev_close: int | None = None

    for day in order:
        occs = occ_by_day[day]
        if not occs:
            continue
        open_, close_ = occs[0], occs[-1]
        if prev_close is None:
            prev_close = open_

        net = close_ - prev_close
        admissions.append(float(max(0, net)))
        discharges.append(float(max(0, -net)))
        labels.append(day)
        prev_close = close_

    return admissions, discharges, labels


def _forecast_series(series: list[float], horizon_days: int) -> list[int]:
    """TimesFM zero-shot on the daily count series (same granularity in/out)."""
    predictor = get_predictor()
    arr = np.array(series[-48:], dtype=np.float32)
    base, _, _ = predictor._run_timesfm_inference(arr, horizon=horizon_days)
    return [max(0, int(round(float(v)))) for v in base[:horizon_days]]


async def run_patient_flow_forecast(
    snapshots: list[dict],
    hospital_id: str,
    unit_id: str,
    days: int = 7,
) -> dict[str, Any]:
    """Builds 7-day admissions/discharges forecasts from snapshot history."""
    adm_series, dis_series, labels = _daily_flows(snapshots)

    if len(adm_series) < 7:
        # Not enough history — pad with means so inference still works
        pad = [float(np.mean(adm_series or [2]))] * (10 - len(adm_series))
        adm_series = pad + adm_series
        dis_pad = [float(np.mean(dis_series or [2]))] * (10 - len(dis_series))
        dis_series = dis_pad + dis_series

    predictor = get_predictor()
    model_ready = predictor._ensure_model_loaded()
    if model_ready:
        pred_adm = _forecast_series(adm_series, days)
        pred_dis = _forecast_series(dis_series, days)
    else:
        # Fallback: recent-mean persistence
        pred_adm = [int(round(np.mean(adm_series[-7:]))) for _ in range(days)]
        pred_dis = [int(round(np.mean(dis_series[-7:]))) for _ in range(days)]

    now = datetime.now(timezone.utc)
    flow = []
    for i in range(days):
        ts = (now + timedelta(days=i + 1)).strftime("%Y-%m-%d")
        flow.append(
            {
                "day": ts,
                "predicted_admissions": max(pred_adm[i], 0),
                "predicted_discharges": max(pred_dis[i], 0),
                "net_flow": pred_adm[i] - pred_dis[i],
            }
        )

    hist_recent = list(zip(labels[-7:], adm_series[-7:], dis_series[-7:]))

    return {
        "hospital_id": hospital_id,
        "unit_id": unit_id,
        "model": predictor.repo_id if model_ready else "mean_persistence_fallback",
        "forecast": flow,
        "recent_history": [
            {"day": d, "admissions": int(a), "discharges": int(dc)}
            for d, a, dc in hist_recent
        ],
    }
