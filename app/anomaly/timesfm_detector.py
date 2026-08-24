"""TimesFM Multi-Layer Anomaly Detection Engine.

Evaluates TimesFM 2.5 zero-shot predictions and real-time hospital context
across three operational dimensions:

1. Capacity Exhaustion (Future Horizon) — When forecasted occupancy or its
   90th-percentile upper bound crosses critical operational limits.
2. Residual Z-Score (Current Moment) — Unexpected spikes in real-time bed
   usage compared to TimesFM's expected baseline.
3. ER Boarder Bottleneck — Accumulation of ER patients waiting for inpatient
   beds beyond safe thresholds.
4. Forecast Volatility — Unusually wide prediction intervals indicating
   erratic, unpredictable operational dynamics.

Architecture:
    ┌──────────────────────────┐  ┌──────────────────────────┐  ┌──────────────────────┐
    │ Capacity Exhaustion      │  │ Residual Z-Score         │  │ Forecast Volatility  │
    │ (Future Forecast)        │  │ (Actual vs Expected)     │  │ (Wide Bounds)        │
    └────────────┬─────────────┘  └────────────┬─────────────┘  └──────────┬───────────┘
                 └──────────────────┬───────────┴───────────────────────────┘
                                    ▼
                         Unified Anomaly Result
                    (Severity, Alerts, Rationale)
"""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from app.models import ForecastResult, HospitalContext


# ============================================================================
# ANOMALY DATA SCHEMAS
# ============================================================================


class AnomalyAlert(BaseModel):
    """A single anomaly alert with type, severity, and clinical context."""

    alert_id: str
    anomaly_type: Literal[
        "CAPACITY_EXHAUSTION_RISK",
        "UNEXPECTED_INFLOW_SPIKE",
        "ER_BOARDER_BOTTLENECK",
        "HIGH_FORECAST_VOLATILITY",
    ]
    severity: Literal["low", "medium", "high", "critical"]
    affected_hour_offset: int  # 0 = now, +1h, +8h, etc.
    affected_timestamp: str
    score: float  # Z-score, peak occupancy %, or bandwidth
    threshold: float  # Threshold that was exceeded
    explanation: str


class TimesFMAnomalyResult(BaseModel):
    """Consolidated result from the multi-layer anomaly detector."""

    detected: bool
    highest_severity: Literal["none", "low", "medium", "high", "critical"]
    total_alerts: int
    alerts: list[AnomalyAlert]
    summary_explanation: str
    generated_at: str


# ============================================================================
# TIMESFM ANOMALY DETECTION ENGINE
# ============================================================================


class TimesFMAnomalyDetector:
    """Multi-layer anomaly detector using TimesFM forecast outputs.

    Evaluates four distinct operational dimensions:
    1. Capacity exhaustion risk (forecast horizon)
    2. Residual inflow spike (current vs. predicted)
    3. ER boarder bottleneck (real-time)
    4. Forecast uncertainty volatility (prediction intervals)

    Configurable thresholds allow tuning for different clinical contexts
    (ICU vs. Med-Surg vs. Pediatric units have different tolerances).
    """

    def __init__(
        self,
        high_occupancy_threshold: float = 0.85,
        critical_capacity_threshold: float = 0.95,
        z_score_threshold: float = 2.5,
        max_uncertainty_bandwidth: float = 0.20,
        er_boarder_threshold: float = 0.08,
    ) -> None:
        """Initialize detector with configurable thresholds.

        Args:
            high_occupancy_threshold: Predicted occupancy triggering "high" alert.
            critical_capacity_threshold: Upper bound triggering "critical" alert.
            z_score_threshold: Z-score for residual spike detection.
            max_uncertainty_bandwidth: Max allowed confidence interval width.
            er_boarder_threshold: ER boarder ratio triggering bottleneck alert.
        """
        self.high_thresh = high_occupancy_threshold
        self.crit_thresh = critical_capacity_threshold
        self.z_thresh = z_score_threshold
        self.max_bandwidth = max_uncertainty_bandwidth
        self.er_boarder_thresh = er_boarder_threshold

    def detect_anomalies(
        self,
        forecast: ForecastResult,
        current_context: HospitalContext,
        er_boarders_count: int = 0,
    ) -> TimesFMAnomalyResult:
        """Run all anomaly detection checks against forecast and context.

        Args:
            forecast: ForecastResult from TimesFM predictor (24h horizon).
            current_context: Current hospital state snapshot.
            er_boarders_count: Number of ER patients waiting for inpatient beds.

        Returns:
            TimesFMAnomalyResult with all detected alerts and severity.
        """
        alerts: list[AnomalyAlert] = []
        now_str = datetime.now(timezone.utc).isoformat()

        # ===================================================================
        # CHECK 1: Capacity Exhaustion Anomalies (24h Forecast Horizon)
        # ===================================================================
        alerts.extend(self._check_capacity_exhaustion(forecast))

        # ===================================================================
        # CHECK 2: Residual Z-Score Anomaly (Current vs. TimesFM Expected)
        # ===================================================================
        alerts.extend(self._check_residual_spike(forecast, current_context))

        # ===================================================================
        # CHECK 3: ER Boarder Accumulation Bottleneck
        # ===================================================================
        alerts.extend(
            self._check_er_boarder_bottleneck(er_boarders_count, current_context)
        )

        # ===================================================================
        # CHECK 4: Forecast Uncertainty Volatility
        # ===================================================================
        alerts.extend(self._check_forecast_volatility(forecast))

        # ===================================================================
        # Consolidate Results
        # ===================================================================
        highest_severity = self._determine_highest_severity(alerts)
        summary = self._build_summary(alerts, highest_severity)

        return TimesFMAnomalyResult(
            detected=len(alerts) > 0,
            highest_severity=highest_severity,
            total_alerts=len(alerts),
            alerts=alerts,
            summary_explanation=summary,
            generated_at=now_str,
        )

    def _check_capacity_exhaustion(
        self, forecast: ForecastResult
    ) -> list[AnomalyAlert]:
        """Detect when forecast occupancy crosses capacity thresholds."""
        alerts: list[AnomalyAlert] = []

        for h, pt in enumerate(forecast.points):
            hour_offset = h + 1

            # Critical: Upper bound >= 95%
            if pt.upper_bound >= self.crit_thresh:
                alerts.append(
                    AnomalyAlert(
                        alert_id=f"ALT-CAP-CRIT-{hour_offset:02d}",
                        anomaly_type="CAPACITY_EXHAUSTION_RISK",
                        severity="critical",
                        affected_hour_offset=hour_offset,
                        affected_timestamp=pt.timestamp,
                        score=round(pt.upper_bound, 4),
                        threshold=self.crit_thresh,
                        explanation=(
                            f"TimesFM 90% upper-bound forecast reaches {pt.upper_bound:.1%} "
                            f"at +{hour_offset}h, exceeding critical threshold "
                            f"({self.crit_thresh:.0%})."
                        ),
                    )
                )
            # High: Predicted mean >= 85%
            elif pt.predicted_occupancy >= self.high_thresh:
                alerts.append(
                    AnomalyAlert(
                        alert_id=f"ALT-CAP-HIGH-{hour_offset:02d}",
                        anomaly_type="CAPACITY_EXHAUSTION_RISK",
                        severity="high",
                        affected_hour_offset=hour_offset,
                        affected_timestamp=pt.timestamp,
                        score=round(pt.predicted_occupancy, 4),
                        threshold=self.high_thresh,
                        explanation=(
                            f"TimesFM mean predicted occupancy reaches "
                            f"{pt.predicted_occupancy:.1%} at +{hour_offset}h, "
                            f"exceeding high-occupancy threshold ({self.high_thresh:.0%})."
                        ),
                    )
                )

        return alerts

    def _check_residual_spike(
        self,
        forecast: ForecastResult,
        current_context: HospitalContext,
    ) -> list[AnomalyAlert]:
        """Detect unexpected deviations between actual and predicted occupancy."""
        alerts: list[AnomalyAlert] = []

        if not forecast.points:
            return alerts

        first_predicted = forecast.points[0].predicted_occupancy
        actual_occupancy = current_context.occupied_beds / max(
            current_context.total_beds, 1
        )

        # Baseline residual standard deviation (3% variance is typical)
        residual_sigma = 0.03
        z_score = abs(actual_occupancy - first_predicted) / residual_sigma

        if z_score >= self.z_thresh:
            alerts.append(
                AnomalyAlert(
                    alert_id="ALT-RESIDUAL-SPIKE-01",
                    anomaly_type="UNEXPECTED_INFLOW_SPIKE",
                    severity="high" if z_score < 3.5 else "critical",
                    affected_hour_offset=0,
                    affected_timestamp=current_context.timestamp,
                    score=round(z_score, 2),
                    threshold=self.z_thresh,
                    explanation=(
                        f"Observed current occupancy ({actual_occupancy:.1%}) deviates "
                        f"significantly from TimesFM expected baseline "
                        f"({first_predicted:.1%}) with Z-score = {z_score:.2f}."
                    ),
                )
            )

        return alerts

    def _check_er_boarder_bottleneck(
        self,
        er_boarders_count: int,
        current_context: HospitalContext,
    ) -> list[AnomalyAlert]:
        """Detect ER boarder accumulation exceeding safe thresholds."""
        alerts: list[AnomalyAlert] = []

        er_boarder_ratio = er_boarders_count / max(current_context.total_beds, 1)

        if er_boarder_ratio >= self.er_boarder_thresh:
            alerts.append(
                AnomalyAlert(
                    alert_id="ALT-ER-BOARDER-01",
                    anomaly_type="ER_BOARDER_BOTTLENECK",
                    severity="high" if er_boarder_ratio < 0.15 else "critical",
                    affected_hour_offset=0,
                    affected_timestamp=current_context.timestamp,
                    score=round(er_boarder_ratio, 4),
                    threshold=self.er_boarder_thresh,
                    explanation=(
                        f"ER Boarder bottleneck detected: {er_boarders_count} patients "
                        f"waiting for inpatient beds ({er_boarder_ratio:.1%} of unit capacity)."
                    ),
                )
            )

        return alerts

    def _check_forecast_volatility(
        self, forecast: ForecastResult
    ) -> list[AnomalyAlert]:
        """Detect unusually wide prediction intervals indicating instability."""
        alerts: list[AnomalyAlert] = []

        for h, pt in enumerate(forecast.points):
            bandwidth = pt.upper_bound - pt.lower_bound
            if bandwidth >= self.max_bandwidth:
                alerts.append(
                    AnomalyAlert(
                        alert_id=f"ALT-VOLATILITY-{h + 1:02d}",
                        anomaly_type="HIGH_FORECAST_VOLATILITY",
                        severity="medium",
                        affected_hour_offset=h + 1,
                        affected_timestamp=pt.timestamp,
                        score=round(bandwidth, 4),
                        threshold=self.max_bandwidth,
                        explanation=(
                            f"Wide TimesFM 90% confidence interval at +{h + 1}h "
                            f"(bandwidth: {bandwidth:.1%}), indicating operational volatility."
                        ),
                    )
                )

        return alerts

    @staticmethod
    def _determine_highest_severity(alerts: list[AnomalyAlert]) -> str:
        """Determine the highest severity across all alerts."""
        severities = [a.severity for a in alerts]
        if "critical" in severities:
            return "critical"
        if "high" in severities:
            return "high"
        if "medium" in severities:
            return "medium"
        if "low" in severities:
            return "low"
        return "none"

    @staticmethod
    def _build_summary(alerts: list[AnomalyAlert], highest_sev: str) -> str:
        """Build a human-readable summary of all detected anomalies."""
        if not alerts:
            return (
                "Nominal operations: No capacity anomalies or statistical "
                "spikes detected by TimesFM."
            )

        crit_count = sum(1 for a in alerts if a.severity == "critical")
        high_count = sum(1 for a in alerts if a.severity == "high")
        med_count = sum(1 for a in alerts if a.severity == "medium")

        return (
            f"Anomaly Detection Alert ({highest_sev.upper()}): "
            f"Identified {len(alerts)} operational anomalies "
            f"({crit_count} Critical, {high_count} High, {med_count} Medium). "
            f"Primary driver: {alerts[0].explanation}"
        )
