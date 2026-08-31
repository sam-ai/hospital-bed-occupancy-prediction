"""Scheduled Temporal workflows for multi-horizon hospital forecasting.

Schedules:
- DailyForecastWorkflow:   9:00 AM daily   -> 24H horizon (24 hourly TimesFM points)
- WeeklyForecastWorkflow:  Monday 8:00 AM  -> 7D horizon (TimesFM 168h resampled to 7 daily points)
- MonthlyForecastWorkflow: 1st of month    -> 6M horizon (trend-projected 6 monthly points)

Pipeline per run:
1. Fetch 48h snapshot history from Elasticsearch ('hospital-snapshots'),
   falling back to the MCP complete-snapshot client when insufficient.
2. Extract TimesFM feature matrices and persist them to 'hospital-features'.
3. Run TimesFM 2.5 inference on a 168-hour horizon.
4. Slice / aggregate into the target horizon and index forecast points
   into 'hospital-forecast-timeline' for UI visualization.
"""

from datetime import datetime, timedelta, timezone

from temporalio import activity, workflow

with workflow.unsafe.imports_passed_through():
    import numpy as np

    from app.anomaly.timesfm_detector import TimesFMAnomalyDetector
    from app.data.elasticsearch_client import (
        fetch_snapshots_from_elasticsearch,
        index_multi_horizon_forecasts_to_elasticsearch,
        save_features_to_elasticsearch,
    )
    from app.data.mock_mcp import HospitalMCPClient
    from app.forecasting.feature_pipeline import TimesFMFeaturePipeline
    from app.forecasting.model import TimesFMHospitalPredictor
    from app.models import (
        CompleteHospitalSnapshot,
        HospitalContext,
    )

mcp_hospital = HospitalMCPClient()
feature_pipeline = TimesFMFeaturePipeline(
    context_window_hours=48, forecast_horizon_hours=168
)
anomaly_detector = TimesFMAnomalyDetector()
_predictor = TimesFMHospitalPredictor()

CONTEXT_WINDOW_HOURS = 48
FULL_HORIZON_HOURS = 168


# ============================================================================
# SHARED PIPELINE HELPERS
# ============================================================================
async def _fetch_context_and_snapshots(
    hospital_id: str, unit_id: str
) -> tuple[list[dict], HospitalContext]:
    """Fetches 48h snapshots from ES, with MCP complete-snapshot fallback.

    Returns (snapshots, current_context).
    """
    snapshots = await fetch_snapshots_from_elasticsearch(
        hospital_id, unit_id, limit=CONTEXT_WINDOW_HOURS
    )

    if len(snapshots) < CONTEXT_WINDOW_HOURS:
        activity.logger.warn(
            "Only %d snapshots in ES (need %d). Falling back to MCP complete snapshot...",
            len(snapshots),
            CONTEXT_WINDOW_HOURS,
        )
        snap: CompleteHospitalSnapshot = await mcp_hospital.get_complete_snapshot(
            hospital_id, unit_id
        )
        fallback = snap.model_dump()
        # Pad to the full context window so feature extraction succeeds
        snapshots = [fallback] * CONTEXT_WINDOW_HOURS

    latest_census = snapshots[-1]["census"]
    context = HospitalContext(
        hospital_id=hospital_id,
        unit_id=unit_id,
        total_beds=latest_census["total_beds"],
        occupied_beds=latest_census["occupied_beds"],
        admissions_24h=latest_census["admissions_24h"],
        discharges_24h=latest_census["discharges_24h"],
        staff_on_duty=latest_census["staff_on_duty"],
        average_los_hours=latest_census["average_los_hours"],
        timestamp=snapshots[-1]["timestamp"],
    )
    return snapshots, context


async def _run_shared_pipeline(
    hospital_id: str, unit_id: str
) -> tuple[list[dict], list[dict], int]:
    """Runs steps 1-3 of the pipeline shared by all three schedules.

    Returns (points_168h, snapshots, total_beds) where points_168h are hourly
    ForecastPoint dumps covering the next 168 hours.
    """
    activity.logger.info("Running shared forecasting pipeline...")

    snapshots, context = await _fetch_context_and_snapshots(hospital_id, unit_id)

    features = feature_pipeline.extract_features(snapshots)
    await save_features_to_elasticsearch(hospital_id, unit_id, features)

    forecast_res = _predictor.forecast(
        past_target=features["past_target"],
        past_covariates=features["past_covariates"],
        future_covariates=features["future_covariates"],
        total_beds=context.total_beds,
        horizon_hours=FULL_HORIZON_HOURS,
    )

    points_168h = [pt.model_dump() for pt in forecast_res.points]
    return points_168h, snapshots, context.total_beds


def detect_anomalies_for_points(
    points_24h: list[dict],
    context: HospitalContext,
    er_boarders_count: int,
):
    """Builds a minimal 24h ForecastResult-compatible object for anomaly detection."""
    from app.models import ForecastPoint, ForecastResult

    now = datetime.now(timezone.utc)
    forecast_obj = ForecastResult(
        model_name="google/timesfm-2.5-200m-transformers",
        model_version="2.5.0",
        horizon_hours=24,
        generated_at=now.isoformat(),
        points=[
            ForecastPoint(**pt)
            for pt in points_24h
        ],
        confidence=0.92,
    )
    return anomaly_detector.detect_anomalies(
        forecast=forecast_obj,
        current_context=context,
        er_boarders_count=er_boarders_count,
    )


# ============================================================================
# AGGREGATION HELPERS
# ============================================================================
def build_daily_7d_forecast(points_168h: list[dict], total_beds: int) -> list[dict]:
    """Resamples 168 hourly predictions into 7 daily aggregated forecast points."""
    daily_points = []
    now = datetime.now(timezone.utc)

    for day_idx in range(7):
        day_slice = points_168h[day_idx * 24 : (day_idx + 1) * 24]
        if not day_slice:
            continue

        mean_occ = sum(p["predicted_occupancy"] for p in day_slice) / len(day_slice)
        peak_occ = max(p["predicted_occupancy"] for p in day_slice)
        lower_bound = min(p["lower_bound"] for p in day_slice)
        upper_bound = max(p["upper_bound"] for p in day_slice)

        daily_points.append(
            {
                "timestamp": (now + timedelta(days=day_idx + 1)).strftime("%Y-%m-%d"),
                "predicted_occupancy": round(mean_occ, 4),
                "peak_occupancy": round(peak_occ, 4),
                "predicted_occupied_beds": int(round(mean_occ * total_beds)),
                "lower_bound": round(lower_bound, 4),
                "upper_bound": round(upper_bound, 4),
            }
        )

    return daily_points


def build_monthly_6m_forecast(points_168h: list[dict], total_beds: int) -> list[dict]:
    """Projects 6 monthly aggregated forecast points from TimesFM trend momentum.

    The foundation model provides a reliable 7-day base; months beyond that are
    extrapolated using the weekly trend observed across the 168h forecast with
    progressively widening uncertainty bands.
    """
    monthly_points = []
    now = datetime.now(timezone.utc)

    week_means = [
        np.mean([p["predicted_occupancy"] for p in points_168h[w * 24 : (w + 1) * 24]])
        for w in range(7)
    ]
    base_occ = float(week_means[-1])
    trend_per_week = float(np.polyfit(np.arange(len(week_means)), week_means, 1)[0])

    for month_idx in range(6):
        weeks_ahead = (month_idx + 1) * 4.33
        mean_occ = base_occ + (trend_per_week * weeks_ahead)
        mean_occ = float(np.clip(mean_occ, 0.30, 0.98))
        peak_occ = min(mean_occ + 0.06, 1.0)
        uncertainty = 0.04 + (month_idx * 0.02)

        monthly_points.append(
            {
                "timestamp": (now + timedelta(days=int(30.44 * (month_idx + 1)))).strftime(
                    "%Y-%m-%d"
                ),
                "predicted_occupancy": round(mean_occ, 4),
                "peak_occupancy": round(peak_occ, 4),
                "predicted_occupied_beds": int(round(mean_occ * total_beds)),
                "lower_bound": round(max(mean_occ - uncertainty, 0.0), 4),
                "upper_bound": round(min(mean_occ + uncertainty, 1.0), 4),
            }
        )

    return monthly_points


# ============================================================================
# TEMPORAL ACTIVITIES
# ============================================================================
@activity.defn
async def run_daily_forecast_activity(hospital_id: str, unit_id: str) -> dict:
    """9:00 AM daily run — 24H hourly forecast + anomaly detection."""
    activity.logger.info("Executing DAILY 24H forecast activity...")

    points_168h, snapshots, total_beds = await _run_shared_pipeline(hospital_id, unit_id)
    points_24h = points_168h[:24]

    latest_census = snapshots[-1]["census"]
    context = HospitalContext(
        hospital_id=hospital_id,
        unit_id=unit_id,
        total_beds=total_beds,
        occupied_beds=latest_census["occupied_beds"],
        admissions_24h=latest_census["admissions_24h"],
        discharges_24h=latest_census["discharges_24h"],
        staff_on_duty=latest_census["staff_on_duty"],
        average_los_hours=latest_census["average_los_hours"],
        timestamp=snapshots[-1]["timestamp"],
    )

    anomaly_res = detect_anomalies_for_points(
        points_24h,
        context,
        er_boarders_count=snapshots[-1]
        .get("er_arrivals", {})
        .get("er_admit_decisions_pending", 0),
    )

    indexed = await index_multi_horizon_forecasts_to_elasticsearch(
        hospital_id=hospital_id,
        unit_id=unit_id,
        horizon_type="24H",
        forecast_points=points_24h,
        total_beds=total_beds,
        anomaly_result=anomaly_res.model_dump(),
    )

    return {
        "status": "SUCCESS",
        "horizon_type": "24H",
        "points_indexed": indexed,
        "total_anomalies_24h": anomaly_res.total_alerts,
        "highest_severity": anomaly_res.highest_severity,
    }


@activity.defn
async def run_weekly_forecast_activity(hospital_id: str, unit_id: str) -> dict:
    """Monday 8:00 AM weekly run — 7D daily-resampled forecast."""
    activity.logger.info("Executing WEEKLY 7D forecast activity...")

    points_168h, _, total_beds = await _run_shared_pipeline(hospital_id, unit_id)
    forecast_7d_points = build_daily_7d_forecast(points_168h, total_beds)

    indexed = await index_multi_horizon_forecasts_to_elasticsearch(
        hospital_id=hospital_id,
        unit_id=unit_id,
        horizon_type="7D",
        forecast_points=forecast_7d_points,
        total_beds=total_beds,
    )

    return {"status": "SUCCESS", "horizon_type": "7D", "points_indexed": indexed}


@activity.defn
async def run_monthly_forecast_activity(hospital_id: str, unit_id: str) -> dict:
    """1st-of-month monthly run — 6M trend-projected forecast."""
    activity.logger.info("Executing MONTHLY 6M forecast activity...")

    points_168h, _, total_beds = await _run_shared_pipeline(hospital_id, unit_id)
    forecast_6m_points = build_monthly_6m_forecast(points_168h, total_beds)

    indexed = await index_multi_horizon_forecasts_to_elasticsearch(
        hospital_id=hospital_id,
        unit_id=unit_id,
        horizon_type="6M",
        forecast_points=forecast_6m_points,
        total_beds=total_beds,
    )

    return {"status": "SUCCESS", "horizon_type": "6M", "points_indexed": indexed}


# ============================================================================
# TEMPORAL WORKFLOW DEFINITIONS
# ============================================================================
@workflow.defn
class DailyForecastWorkflow:
    """Cron schedule: '0 9 * * *' — daily 9:00 AM."""

    @workflow.run
    async def run(self, hospital_id: str = "HOSPITAL-MAIN-01", unit_id: str = "ICU-EAST") -> dict:
        return await workflow.execute_activity(
            run_daily_forecast_activity,
            args=[hospital_id, unit_id],
            start_to_close_timeout=timedelta(minutes=10),
        )


@workflow.defn
class WeeklyForecastWorkflow:
    """Cron schedule: '0 8 * * 1' — every Monday 8:00 AM."""

    @workflow.run
    async def run(self, hospital_id: str = "HOSPITAL-MAIN-01", unit_id: str = "ICU-EAST") -> dict:
        return await workflow.execute_activity(
            run_weekly_forecast_activity,
            args=[hospital_id, unit_id],
            start_to_close_timeout=timedelta(minutes=10),
        )


@workflow.defn
class MonthlyForecastWorkflow:
    """Cron schedule: '0 8 1 * *' — 1st of every month 8:00 AM."""

    @workflow.run
    async def run(self, hospital_id: str = "HOSPITAL-MAIN-01", unit_id: str = "ICU-EAST") -> dict:
        return await workflow.execute_activity(
            run_monthly_forecast_activity,
            args=[hospital_id, unit_id],
            start_to_close_timeout=timedelta(minutes=10),
        )
