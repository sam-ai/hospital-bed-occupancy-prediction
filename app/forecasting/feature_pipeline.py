"""TimesFM 2.5 Feature Engineering Pipeline.

Transforms raw hourly hospital snapshots (CompleteHospitalSnapshot records)
into structured NumPy feature arrays for TimesFM foundation model inference.

Feature Categories:
1. Past Target (T hours): Hourly bed occupancy rate — the primary forecast variable.
2. Dynamic Past Covariates (T hours): High-frequency operational drivers that
   influence occupancy but are NOT known in advance.
3. Known Future Covariates (H hours): Deterministic data known for the forecast
   horizon (scheduled surgeries, calendar features).
4. Static Covariates: Time-invariant unit metadata (bed capacity, avg LOS).

Usage:
    from app.forecasting.feature_pipeline import TimesFMFeaturePipeline

    pipeline = TimesFMFeaturePipeline(context_window_hours=48, forecast_horizon_hours=24)
    features = pipeline.extract_features(snapshots)
    # features["past_target"]        -> shape (48,)
    # features["past_covariates"]    -> shape (48, 4)
    # features["future_covariates"]  -> shape (24, 6)
    # features["static_covariates"]  -> shape (3,)
"""

import math
from datetime import datetime, timedelta
from typing import Any

import numpy as np


class TimesFMFeaturePipeline:
    """Transforms raw hourly hospital snapshots into structured feature matrices
    compatible with Google TimesFM 2.5.

    Architecture:
        Past Target + Dynamic Covariates → Encoder (context understanding)
        Known Future Covariates → Decoder (horizon conditioning)
        Static Covariates → Global conditioning

    Feature Matrix Shapes:
        past_target:        (context_window,)         — occupancy rate [0.0, 1.0]
        past_covariates:    (context_window, 4)       — ER pressure, discharge liquidity, outbreak
        future_covariates:  (forecast_horizon, 6)     — elective rate, hour/dow cycles, weekend
        static_covariates:  (3,)                      — total_beds, avg_los, staff_ratio
    """

    # Column names for documentation and debugging
    PAST_COVARIATE_NAMES = [
        "er_boarder_pressure",
        "er_waiting_pressure",
        "discharge_liquidity",
        "outbreak_severity_score",
    ]
    FUTURE_COVARIATE_NAMES = [
        "scheduled_elective_rate",
        "hour_sin",
        "hour_cos",
        "dow_sin",
        "dow_cos",
        "is_weekend",
        "is_holiday",
    ]
    STATIC_COVARIATE_NAMES = [
        "total_beds",
        "average_los_hours",
        "staff_ratio",
    ]

    def __init__(
        self,
        context_window_hours: int = 48,
        forecast_horizon_hours: int = 24,
    ) -> None:
        """Initialize the feature pipeline.

        Args:
            context_window_hours: Number of past hours to use as context (default 48).
            forecast_horizon_hours: Number of future hours to forecast (default 24).
        """
        self.context_window = context_window_hours
        self.forecast_horizon = forecast_horizon_hours

    def extract_features(self, snapshots: list[dict[str, Any]]) -> dict[str, Any]:
        """Process a list of raw snapshot dictionaries into TimesFM feature arrays.

        Args:
            snapshots: List of CompleteHospitalSnapshot-compatible dicts,
                      sorted chronologically. Must have at least `context_window` entries.

        Returns:
            Dictionary with keys:
                - past_target: np.ndarray shape (context_window,)
                - past_covariates: np.ndarray shape (context_window, 4)
                - future_covariates: np.ndarray shape (forecast_horizon, 6)
                - static_covariates: np.ndarray shape (3,)
                - timestamps_future: list[str] of ISO timestamps for forecast hours

        Raises:
            ValueError: If insufficient snapshots provided.
        """
        if len(snapshots) < self.context_window:
            raise ValueError(
                f"Need at least {self.context_window} snapshots, got {len(snapshots)}"
            )

        # Use the last N snapshots for historical context window
        history_snapshots = snapshots[-self.context_window:]

        # ===================================================================
        # 1. TARGET TIME SERIES (Past T Hours)
        #    Primary variable: hourly bed occupancy rate [0.0, 1.0]
        # ===================================================================
        past_occupancy_rate = np.array(
            [
                s["census"]["occupied_beds"] / max(s["census"]["total_beds"], 1)
                for s in history_snapshots
            ],
            dtype=np.float32,
        )

        # ===================================================================
        # 2. DYNAMIC PAST COVARIATES (Past T Hours)
        #    Operational drivers NOT known in advance
        # ===================================================================

        # ER Boarder Pressure: patients waiting for inpatient beds (converts in 1-4h)
        past_er_boarder_pressure = np.array(
            [
                s["er_arrivals"]["er_admit_decisions_pending"]
                / max(s["census"]["total_beds"], 1)
                for s in history_snapshots
            ],
            dtype=np.float32,
        )

        # ER Waiting Pressure: total ER volume (predicts demand 4-8h out)
        past_er_waiting_pressure = np.array(
            [
                s["er_arrivals"]["er_current_waiting_count"]
                / max(s["census"]["total_beds"], 1)
                for s in history_snapshots
            ],
            dtype=np.float32,
        )

        # Discharge Liquidity: ratio of pending discharges to occupied beds
        # Higher = more beds about to open up
        past_discharge_liquidity = np.array(
            [
                s["census"]["pending_discharges_today"]
                / max(s["census"]["occupied_beds"], 1)
                for s in history_snapshots
            ],
            dtype=np.float32,
        )

        # Outbreak Severity Score: epidemic index (0.0 = calm, 1.0 = peak)
        past_outbreak_score = np.array(
            [
                self._extract_signal_value(
                    s.get("external_signals", []), "flu_outbreak_index"
                )
                for s in history_snapshots
            ],
            dtype=np.float32,
        )

        # Stack into matrix: shape (context_window, 4)
        past_covariates_matrix = np.column_stack(
            [
                past_er_boarder_pressure,
                past_er_waiting_pressure,
                past_discharge_liquidity,
                past_outbreak_score,
            ]
        )

        # ===================================================================
        # 3. KNOWN FUTURE COVARIATES (Future H Hours)
        #    Deterministic data known in advance for the forecast horizon
        # ===================================================================
        last_timestamp_str = history_snapshots[-1]["timestamp"]
        last_dt = datetime.fromisoformat(last_timestamp_str)
        total_beds = history_snapshots[-1]["census"]["total_beds"]

        # Generate future timestamps
        future_timestamps = [
            last_dt + timedelta(hours=i + 1) for i in range(self.forecast_horizon)
        ]

        # Cyclical hour-of-day encoding (captures diurnal patterns)
        future_hour_sin = np.array(
            [math.sin(2 * math.pi * dt.hour / 24.0) for dt in future_timestamps],
            dtype=np.float32,
        )
        future_hour_cos = np.array(
            [math.cos(2 * math.pi * dt.hour / 24.0) for dt in future_timestamps],
            dtype=np.float32,
        )

        # Cyclical day-of-week encoding (captures weekly patterns)
        future_dow_sin = np.array(
            [math.sin(2 * math.pi * dt.weekday() / 7.0) for dt in future_timestamps],
            dtype=np.float32,
        )
        future_dow_cos = np.array(
            [math.cos(2 * math.pi * dt.weekday() / 7.0) for dt in future_timestamps],
            dtype=np.float32,
        )

        # Weekend binary flag
        future_is_weekend = np.array(
            [1.0 if dt.weekday() >= 5 else 0.0 for dt in future_timestamps],
            dtype=np.float32,
        )

        # Scheduled elective surgery rate (known in advance from OR schedule)
        # Elective surgeries happen weekdays 07:00-11:00 only
        # Avg ~3 elective admissions per hour during OR window
        avg_elective_per_hour = 3.0
        future_scheduled_elective_rate = np.array(
            [
                (avg_elective_per_hour / total_beds)
                if dt.weekday() < 5 and 7 <= dt.hour <= 11
                else 0.0
                for dt in future_timestamps
            ],
            dtype=np.float32,
        )

        # Stack into matrix: shape (forecast_horizon, 7)
        future_covariates_matrix = np.column_stack(
            [
                future_scheduled_elective_rate,
                future_hour_sin,
                future_hour_cos,
                future_dow_sin,
                future_dow_cos,
                future_is_weekend,
                np.array(
                    [1.0 if self._is_holiday(dt) else 0.0 for dt in future_timestamps],
                    dtype=np.float32,
                ),
            ]
        )

        # ===================================================================
        # 4. STATIC COVARIATES (Time-invariant unit metadata)
        # ===================================================================
        latest = history_snapshots[-1]
        static_features = np.array(
            [
                float(latest["census"]["total_beds"]),
                float(latest["census"]["average_los_hours"]),
                latest["census"]["staff_on_duty"]
                / max(latest["census"]["occupied_beds"], 1),
            ],
            dtype=np.float32,
        )

        return {
            "past_target": past_occupancy_rate,             # Shape: (48,)
            "past_covariates": past_covariates_matrix,      # Shape: (48, 4)
            "future_covariates": future_covariates_matrix,  # Shape: (24, 6)
            "static_covariates": static_features,           # Shape: (3,)
            "timestamps_future": [dt.isoformat() for dt in future_timestamps],
        }

    # Fixed-date US holidays (month, day) + winter surge window
    HOLIDAYS_FIXED = {
        (1, 1),   # New Year's Day
        (7, 4),   # Independence Day
        (11, 11), # Veterans Day
        (12, 25), # Christmas Day
    }
    HOLIDAY_WINDOW = {(12, 24), (12, 26), (12, 27), (12, 28), (12, 29), (12, 30), (12, 31)}

    @classmethod
    def _is_holiday(cls, dt: datetime) -> bool:
        """True on fixed holidays or the Dec 24 - Jan 1 winter window."""
        md = (dt.month, dt.day)
        return md in cls.HOLIDAYS_FIXED or md in cls.HOLIDAY_WINDOW

    @staticmethod
    def _extract_signal_value(signals: list[dict[str, Any]], signal_type: str) -> float:
        """Extract a numerical value from the external signals list by type.

        Args:
            signals: List of signal dicts with 'signal_type' and 'value' keys.
            signal_type: The signal_type to search for.

        Returns:
            Float value of the matching signal, or 0.0 if not found.
        """
        for sig in signals:
            if sig.get("signal_type") == signal_type:
                val = sig.get("value", 0.0)
                try:
                    return float(val)
                except (TypeError, ValueError):
                    return 0.0
        return 0.0
