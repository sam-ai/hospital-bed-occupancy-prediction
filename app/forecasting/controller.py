from app.forecasting.service import ForecastService
from app.models import ExternalSignal, ForecastResult, HospitalContext


class ForecastController:
    """Controller with input validation for the forecasting pipeline."""

    def __init__(self) -> None:
        self.service = ForecastService()

    async def run_forecast(
        self,
        context: HospitalContext,
        signals: list[ExternalSignal],
    ) -> ForecastResult:
        """Validate inputs and execute the forecast pipeline."""
        if context.total_beds <= 0:
            raise ValueError("Total beds must be positive to forecast.")
        return await self.service.forecast_occupancy(context, signals)
