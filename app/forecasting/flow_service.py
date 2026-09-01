"""Patient-flow forecasting: daily admissions & discharges (guide §3.1-3.2).

Derives daily admission/discharge count series from historical snapshot
occupancy deltas, then runs TimesFM zero-shot per series for a 7-day
horizon. Series semantics:

    admissions(t) = max(0, occupancy(t) - occupancy(t-1))   # inflow-driven
    discharges(t) = max(0, occupancy(t-1) - occupancy(t))   # outflow-driven

Daily aggregation sums the hourly deltas per calendar day; the resulting
~30-day daily series becomes the forecast context.

Ward-aware additions:
- Admission-source breakdown (ER-direct / elective / inter-ward transfers)
  derived from each ward's admission mix in app/data/wards.py.
- Next-24h anticipated flows compared against the trailing baseline
  average ("trend since last 3 months" — uses whatever history exists).
- Results persisted to the hospital-patient-flow ES index.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np

from app.data.wards import get_ward
from app.forecasting.strategy_service import get_predictor


def _daily_flows(snapshots: list[dict]) -> tuple[list[float], list[float], list[str]]:
    """Returns (daily_admissions, daily_discharges, day_labels).

    Sums intra-day hourly occupancy deltas so churn is captured:
    a ward admitting 3 and discharging 3 the same day reports both,
    instead of netting to zero on the day's open->close delta.
    """
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

        adm = 0.0
        dis = 0.0
        prev = prev_close if prev_close is not None else occs[0]
        for occ in occs:
            delta = occ - prev
            if delta > 0:
                adm += delta
            else:
                dis += -delta
            prev = occ
        prev_close = occs[-1]

        admissions.append(adm)
        discharges.append(dis)
        labels.append(day)

    return admissions, discharges, labels


async def _forecast_series(series: list[float], horizon_days: int) -> list[int]:
    """TimesFM zero-shot on the daily count series (same granularity in/out)."""
    predictor = await get_predictor()
    arr = np.array(series[-48:], dtype=np.float32)
    base, _, _ = predictor._run_timesfm_inference(arr, horizon=horizon_days)
    # Coerce NaN/inf to safe fallback (recent mean of input series) to avoid
    # ValueError when int(round(NaN)) is attempted downstream.
    fallback = float(np.mean(series)) if series else 0.0
    safe: list[int] = []
    for v in base[:horizon_days]:
        fv = float(v)
        if fv != fv or fv in (float("inf"), float("-inf")):  # NaN/inf check
            fv = fallback
        safe.append(max(0, int(round(fv))))
    return safe


def _split_admission_sources(total_admissions: int, unit_id: str) -> dict[str, int]:
    """Splits a predicted admission count into ER-direct / elective /
    inter-ward-transfer shares using the ward's admission-source mix."""
    ward = get_ward(unit_id)

    er = total_admissions * ward.er_admit_weight
    el = total_admissions * ward.elective_weight
    tr = total_admissions * ward.transfer_in_weight

    # Largest-remainder rounding so sources always sum to the total
    er_i, el_i, tr_i = int(er), int(el), int(tr)
    remainder = total_admissions - (er_i + el_i + tr_i)
    fracs = sorted(
        [(er - er_i, "er_direct"), (el - el_i, "elective"), (tr - tr_i, "icu_transfers")],
        reverse=True,
    )
    for i in range(remainder):
        _, key = fracs[i % len(fracs)]
        if key == "er_direct":
            er_i += 1
        elif key == "elective":
            el_i += 1
        else:
            tr_i += 1

    return {"er_direct": er_i, "elective": el_i, "icu_transfers": tr_i}


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

    predictor = await get_predictor()
    model_ready = predictor._ensure_model_loaded()
    if model_ready:
        pred_adm = await _forecast_series(adm_series, days)
        pred_dis = await _forecast_series(dis_series, days)
    else:
        # Fallback: recent-mean persistence
        pred_adm = [int(round(np.mean(adm_series[-7:]))) for _ in range(days)]
        pred_dis = [int(round(np.mean(dis_series[-7:]))) for _ in range(days)]

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    flow = []
    for i in range(days):
        ts = (now + timedelta(days=i + 1)).strftime("%Y-%m-%d")
        flow.append(
            {
                "day": ts,
                "predicted_admissions": max(pred_adm[i], 0),
                "predicted_discharges": max(pred_dis[i], 0),
                "net_flow": pred_adm[i] - pred_dis[i],
                **_split_admission_sources(max(pred_adm[i], 0), unit_id),
            }
        )

    hist_recent = list(zip(labels[-7:], adm_series[-7:], dis_series[-7:]))

    # ── Next-24h anticipated flows vs trailing baseline ──
    next_adm = max(flow[0]["predicted_admissions"], 0)
    next_dis = max(flow[0]["predicted_discharges"], 0)

    baseline_window = min(len(adm_series), 90)
    baseline_adm = float(np.mean(adm_series[-baseline_window:]))
    baseline_dis = float(np.mean(dis_series[-baseline_window:]))

    def _trend(predicted: float, baseline: float) -> str:
        if baseline <= 0:
            return predicted > 0 and "up" or "flat"
        delta_pct = (predicted - baseline) / baseline * 100
        if delta_pct > 10:
            return "up"
        if delta_pct < -10:
            return "down"
        return "flat"

    next_24h = {
        "predicted_admissions": next_adm,
        **_split_admission_sources(next_adm, unit_id),
        "predicted_discharges": next_dis,
        "admissions_trend": _trend(next_adm, baseline_adm),
        "discharges_trend": _trend(next_dis, baseline_dis),
        "baseline_admissions": round(baseline_adm, 2),
        "baseline_discharges": round(baseline_dis, 2),
        "baseline_window_days": baseline_window,
    }

    result = {
        "hospital_id": hospital_id,
        "unit_id": unit_id,
        "model": predictor.repo_id if model_ready else "mean_persistence_fallback",
        "forecast_date": today_str,
        "next_24h": next_24h,
        "forecast": flow,
        "recent_history": [
            {"day": d, "admissions": int(a), "discharges": int(dc)}
            for d, a, dc in hist_recent
        ],
    }
    return result


async def persist_patient_flow_forecast(result: dict[str, Any]) -> None:
    """Persists the next-24h flow record to ES (best-effort, non-fatal)."""
    try:
        from app.data.elasticsearch_client import save_patient_flow_record

        nx = result.get("next_24h", {})
        await save_patient_flow_record(
            {
                "forecast_date": result.get("forecast_date"),
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "hospital_id": result.get("hospital_id"),
                "unit_id": result.get("unit_id"),
                "predicted_admissions": nx.get("predicted_admissions", 0),
                "er_direct": nx.get("er_direct", 0),
                "elective": nx.get("elective", 0),
                "icu_transfers": nx.get("icu_transfers", 0),
                "predicted_discharges": nx.get("predicted_discharges", 0),
                "baseline_admissions_90d": nx.get("baseline_admissions"),
                "baseline_discharges_90d": nx.get("baseline_discharges"),
            }
        )
    except Exception as e:
        print(f"[!] Patient-flow persistence skipped ({e})")
