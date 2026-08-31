"""Generate back-dated 24H forecasts anchored at previous days (append-only).

Purpose: the daily forecast activity always anchors at "now", so only the
current day's 24H forecast exists. This script produces 24H forecasts anchored
at each of the last N days for every ward, tagged with that day's
`forecast_date`, so the UI date-picker / `?date=` API can show
"what we would have forecast on day X" alongside actuals.

It reuses the exact production pipeline pieces:
  fetch_snapshots(as_of) -> feature_pipeline -> predictor -> anomaly detector
  -> index_multi_horizon_forecasts(forecast_date=<anchor>)

SAFETY:
  - Writes ONLY new forecast-timeline docs keyed by the anchor date
    ({date}_{hospital}_{unit}_24H_STEP###). It never deletes and never
    overwrites the live "today" forecast (different date key).
  - Skips a given (ward, date) if <48h of prior context exists.

Usage:
    uv run python scripts/backdate_forecasts.py --days-back 5
    uv run python scripts/backdate_forecasts.py --days-back 5 --unit ICU-EAST
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.anomaly.timesfm_detector import TimesFMAnomalyDetector
from app.data.elasticsearch_client import (
    fetch_snapshots_from_elasticsearch,
    index_multi_horizon_forecasts_to_elasticsearch,
)
from app.data.wards import WARDS
from app.forecasting.feature_pipeline import TimesFMFeaturePipeline
from app.forecasting.model import TimesFMHospitalPredictor
from app.models import ForecastPoint, ForecastResult, HospitalContext

HOSPITAL_ID = "HOSPITAL-MAIN-01"
CONTEXT_WINDOW_HOURS = 48

feature_pipeline = TimesFMFeaturePipeline(
    context_window_hours=48, forecast_horizon_hours=168
)
anomaly_detector = TimesFMAnomalyDetector()


def _detect(points_24h, context, er_boarders):
    forecast_obj = ForecastResult(
        model_name="google/timesfm-2.5-200m-transformers",
        model_version="2.5.0",
        horizon_hours=24,
        generated_at=datetime.now(timezone.utc).isoformat(),
        points=[ForecastPoint(**pt) for pt in points_24h],
        confidence=0.92,
    )
    return anomaly_detector.detect_anomalies(
        forecast=forecast_obj, current_context=context, er_boarders_count=er_boarders
    )


async def backdate_ward(predictor, unit_id: str, total_beds: int, anchor_dates: list[str]) -> None:
    for anchor in anchor_dates:
        # Context = 48h of snapshots strictly before the anchor day start.
        as_of = f"{anchor}T00:00:00+00:00"
        snapshots = await fetch_snapshots_from_elasticsearch(
            HOSPITAL_ID, unit_id, limit=CONTEXT_WINDOW_HOURS, as_of=as_of
        )
        if len(snapshots) < CONTEXT_WINDOW_HOURS:
            print(f"    {unit_id:<14} {anchor}: only {len(snapshots)} ctx snapshots "
                  f"(need {CONTEXT_WINDOW_HOURS}) — skipped")
            continue

        features = feature_pipeline.extract_features(snapshots)
        forecast_res = predictor.forecast(
            past_target=features["past_target"],
            past_covariates=features["past_covariates"],
            future_covariates=features["future_covariates"],
            total_beds=total_beds,
            horizon_hours=24,
        )
        points_24h = [pt.model_dump() for pt in forecast_res.points]

        # Re-stamp point timestamps to the anchor day (hourly).
        base = datetime.fromisoformat(as_of)
        for i, pt in enumerate(points_24h):
            pt["timestamp"] = (base + timedelta(hours=i)).isoformat()

        latest_census = snapshots[-1]["census"]
        context = HospitalContext(
            hospital_id=HOSPITAL_ID,
            unit_id=unit_id,
            total_beds=total_beds,
            occupied_beds=latest_census["occupied_beds"],
            admissions_24h=latest_census["admissions_24h"],
            discharges_24h=latest_census["discharges_24h"],
            staff_on_duty=latest_census["staff_on_duty"],
            average_los_hours=latest_census["average_los_hours"],
            timestamp=snapshots[-1]["timestamp"],
        )
        er_boarders = snapshots[-1].get("er_arrivals", {}).get("er_admit_decisions_pending", 0)

        try:
            anomaly_res = _detect(points_24h, context, er_boarders)
            anomaly_dump = anomaly_res.model_dump()
            sev = anomaly_res.highest_severity
        except Exception as e:  # noqa: BLE001
            print(f"    {unit_id:<14} {anchor}: anomaly detect skipped ({e})")
            anomaly_dump = None
            sev = "none"

        indexed = await index_multi_horizon_forecasts_to_elasticsearch(
            hospital_id=HOSPITAL_ID,
            unit_id=unit_id,
            horizon_type="24H",
            forecast_points=points_24h,
            total_beds=total_beds,
            anomaly_result=anomaly_dump,
            forecast_date=anchor,
        )
        print(f"    [OK] {unit_id:<14} {anchor}: indexed {indexed} pts (severity={sev})")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Back-date 24H forecasts for previous days.")
    parser.add_argument("--days-back", type=int, default=5,
                        help="Generate anchors for the last N days (1..N).")
    parser.add_argument("--unit", default=None, help="Limit to a single ward (default: all).")
    args = parser.parse_args()

    today = datetime.now(timezone.utc).date()
    # Anchor dates: yesterday back to N days ago (exclude today; today already exists).
    anchor_dates = [
        (today - timedelta(days=d)).strftime("%Y-%m-%d")
        for d in range(1, args.days_back + 1)
    ]
    print(f"[*] Back-dating 24H forecasts for anchors: {anchor_dates}")

    wards = [w for w in WARDS if (args.unit is None or w.unit_id == args.unit)]
    predictor = TimesFMHospitalPredictor()
    try:
        for ward in wards:
            print(f"[*] Ward {ward.unit_id} (beds={ward.total_beds})")
            await backdate_ward(predictor, ward.unit_id, ward.total_beds, anchor_dates)
    finally:
        from app.data.elasticsearch_client import es_client
        await es_client.close()

    print("\n[SUCCESS] Back-dated forecasts generated (append-only).")


if __name__ == "__main__":
    asyncio.run(main())
