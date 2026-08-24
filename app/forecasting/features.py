from app.models import ExternalSignal, HospitalContext


class ForecastFeatures:
    """Engineered features derived from hospital context and external signals."""

    def __init__(
        self,
        occupancy_rate: float,
        net_admission_pressure: float,
        staff_ratio: float,
        external_signal_score: float,
    ):
        self.occupancy_rate = occupancy_rate
        self.net_admission_pressure = net_admission_pressure
        self.staff_ratio = staff_ratio
        self.external_signal_score = external_signal_score


def build_features(context: HospitalContext, signals: list[ExternalSignal]) -> ForecastFeatures:
    """Transform raw hospital context and signals into model-ready features."""
    occupancy_rate = context.occupied_beds / max(context.total_beds, 1)
    net_admission_pressure = (context.admissions_24h - context.discharges_24h) / max(context.total_beds, 1)
    staff_ratio = context.staff_on_duty / max(context.occupied_beds, 1)

    # Count high-severity increasing signals as risk multiplier
    high_sev_count = sum(
        1 for s in signals
        if s.severity in ["medium", "high", "critical"] and s.direction == "increasing"
    )
    signal_score = high_sev_count / max(len(signals), 1)

    return ForecastFeatures(
        occupancy_rate=occupancy_rate,
        net_admission_pressure=net_admission_pressure,
        staff_ratio=staff_ratio,
        external_signal_score=signal_score,
    )
