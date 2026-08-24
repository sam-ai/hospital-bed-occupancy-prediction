"""Forecast service wrapping the TimesFM 2.5 foundation model.

Provides two execution paths:
1. Full feature pipeline mode: Uses TimesFMFeaturePipeline + TimesFMHospitalPredictor
   for maximum accuracy with structured covariates.
2. Basic mode: Uses predict_from_features() for backward compatibility
   with the existing HospitalContext-based pipeline.
"""

from app.forecasting.features import build_features
from app.forecasting.model import TimesFMHospitalPredictor
from app.models import ExternalSignal, ForecastResult, HospitalContext


class ForecastService:
    """Service layer for hospital bed occupancy forecasting.

    Uses TimesFM 2.5 foundation model with covariate conditioning.
    Falls back to trend-based inference if model weights are unavailable.
    """

    def __init__(self) -> None:
        # Instantiate TimesFM 2.5 predictor (lazy-loads on first call)
        self.predictor = TimesFMHospitalPredictor()

    async def forecast_occupancy(
        self,
        context: HospitalContext,
        signals: list[ExternalSignal],
        horizon_hours: int = 24,
    ) -> ForecastResult:
        """Build features and run TimesFM zero-shot inference.

        Uses the backward-compatible interface (ForecastFeatures + historical counts).
        For maximum accuracy with the full 4-pillar snapshot data, use
        forecast_from_snapshot() instead.

        Args:
            context: Hospital context including historical occupancy counts.
            signals: External epidemiological/environmental signals.
            horizon_hours: Number of hours to forecast.

        Returns:
            ForecastResult with predicted occupancy and confidence intervals.
        """
        # Extract feature covariates
        features = build_features(context, signals)

        # Execute TimesFM prediction using backward-compatible interface
        forecast = self.predictor.predict_from_features(
            features=features,
            historical_counts=context.historical_occupancy_counts,
            total_beds=context.total_beds,
            horizon_hours=horizon_hours,
        )

        return forecast

    async def forecast_from_pipeline(
        self,
        past_target,
        past_covariates,
        future_covariates,
        total_beds: int = 50,
        horizon_hours: int = 24,
    ) -> ForecastResult:
        """Run forecast using pre-computed feature pipeline arrays.

        This is the preferred path when using CompleteHospitalSnapshot data
        processed through TimesFMFeaturePipeline.

        Args:
            past_target: np.ndarray shape (48,) — occupancy rates.
            past_covariates: np.ndarray shape (48, 4) — dynamic covariates.
            future_covariates: np.ndarray shape (24, 6) — known future covariates.
            total_beds: Total bed capacity.
            horizon_hours: Forecast horizon.

        Returns:
            ForecastResult with covariate-conditioned predictions.
        """
        return self.predictor.forecast(
            past_target=past_target,
            past_covariates=past_covariates,
            future_covariates=future_covariates,
            total_beds=total_beds,
            horizon_hours=horizon_hours,
        )
