from app.llm import get_llm
from app.models import AnomalyResult, ForecastResult, HospitalContext

_llm = get_llm()


class AnomalyService:
    """Threshold-based anomaly detection on forecast results.

    Uses deterministic thresholds for detection, then optionally enhances
    the explanation with LLM-powered contextual analysis.
    """

    async def detect(self, context: HospitalContext, forecast: ForecastResult) -> AnomalyResult:
        """Analyze forecast for capacity exhaustion risks.

        Thresholds:
            - Upper bound >= 95% → critical (capacity exhaustion risk)
            - Predicted >= 85% → high (high occupancy alert)
            - Otherwise → none
        """
        max_upper = max(p.upper_bound for p in forecast.points) if forecast.points else 0.0
        max_pred = max(p.predicted_occupancy for p in forecast.points) if forecast.points else 0.0

        if max_upper >= 0.95:
            base_result = AnomalyResult(
                detected=True,
                anomaly_type="capacity_exhaustion_risk",
                severity="critical",
                score=max_upper,
                explanation=(
                    "Predicted upper-bound occupancy reaches or exceeds 95% "
                    "threshold within forecast horizon."
                ),
                affected_metric="occupancy",
            )
        elif max_pred >= 0.85:
            base_result = AnomalyResult(
                detected=True,
                anomaly_type="high_occupancy_alert",
                severity="high",
                score=max_pred,
                explanation="Predicted occupancy exceeds high threshold (>85%).",
                affected_metric="occupancy",
            )
        else:
            return AnomalyResult(
                detected=False,
                severity="none",
                score=max_pred,
                explanation="Occupancy within nominal bounds.",
            )

        # Enhance explanation with LLM analysis
        base_result.explanation = await self._llm_enhance_explanation(
            base_result, context, forecast, max_upper, max_pred
        )
        return base_result

    async def _llm_enhance_explanation(
        self,
        result: AnomalyResult,
        context: HospitalContext,
        forecast: ForecastResult,
        max_upper: float,
        max_pred: float,
    ) -> str:
        """Use LLM to generate a richer contextual anomaly explanation."""
        if not _llm:
            return result.explanation

        try:
            # Build forecast summary for the prompt
            forecast_snippet = "\n".join(
                f"  +{i + 1}h: predicted={p.predicted_occupancy:.1%}, "
                f"range=[{p.lower_bound:.1%}, {p.upper_bound:.1%}]"
                for i, p in enumerate(forecast.points[:6])  # First 6 hours
            )
            prompt = (
                f"System: You are an AI Hospital Capacity Anomaly Analyst.\n"
                f"Context: Hospital {context.hospital_id}, Unit {context.unit_id}. "
                f"Beds: {context.occupied_beds}/{context.total_beds} occupied "
                f"({context.occupied_beds / max(context.total_beds, 1):.0%}), "
                f"Staff: {context.staff_on_duty}, "
                f"Avg LOS: {context.average_los_hours:.1f}h.\n"
                f"Anomaly: {result.anomaly_type} (severity={result.severity}, "
                f"score={result.score:.1%}).\n"
                f"Forecast Horizon:\n{forecast_snippet}\n\n"
                f"Task: Provide a concise clinical operations explanation of this "
                f"anomaly (2-3 sentences). Include: (1) what the data shows, "
                f"(2) the operational risk, and (3) one immediate action. "
                f"Keep response under 50 words."
            )
            response = await _llm.ainvoke(prompt)
            enhanced = str(response.content).strip()
            if enhanced:
                return f"{result.explanation} LLM Analysis: {enhanced}"
        except Exception:
            pass

        return result.explanation
