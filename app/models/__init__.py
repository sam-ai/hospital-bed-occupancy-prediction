from typing import Any, Literal
from pydantic import BaseModel, Field


class HospitalRequest(BaseModel):
    """Input request to trigger the hospital capacity workflow."""
    request_id: str
    hospital_id: str
    unit_id: str
    objective: str


class HospitalContext(BaseModel):
    """Current state of a hospital unit retrieved from MCP."""
    hospital_id: str
    unit_id: str
    total_beds: int
    occupied_beds: int
    admissions_24h: int
    discharges_24h: int
    staff_on_duty: int
    average_los_hours: float
    timestamp: str
    # Sequence of past hourly occupancy counts (e.g., past 48 hours) for TimesFM
    historical_occupancy_counts: list[int] = Field(default_factory=list)


class DataQuality(BaseModel):
    """Validation result for hospital data."""
    status: str
    quality_score: float
    issues: list[str] = Field(default_factory=list)


class ExternalSignal(BaseModel):
    """External epidemiological or environmental signal."""
    signal_type: str
    value: Any
    direction: Literal["increasing", "stable", "decreasing"]
    severity: Literal["low", "medium", "high", "critical"]
    confidence: float


# ===========================================================================
# Expanded Domain Schemas (4-Pillar Hospital Data Model)
# ===========================================================================

class WardCensusContext(BaseModel):
    """Detailed ward/unit census state (Pillar 1: Historical Census)."""
    unit_id: str
    unit_type: Literal["ICU", "MED_SURG", "STEP_DOWN", "PEDIATRIC", "ER"]
    total_beds: int
    occupied_beds: int
    blocked_beds: int = 0                    # Beds unavailable (cleaning/isolation)
    admissions_24h: int
    discharges_24h: int
    pending_discharges_today: int = 0        # Expected bed openings today
    staff_on_duty: int
    average_los_hours: float
    bed_turnover_time_hours: float = 2.0     # Avg time to clean and prep a bed


class ERArrivalsContext(BaseModel):
    """ER real-time volume metrics (Pillar 2: ER Arrivals)."""
    er_current_waiting_count: int            # Patients currently in ER waiting room
    er_admit_decisions_pending: int          # ER Boarders waiting for inpatient beds
    er_high_acuity_arrivals_last_6h: int     # Critical cases (Triage 1-2) likely needing beds


class ScheduledCasesContext(BaseModel):
    """Elective/scheduled surgical case load (Pillar 3: Scheduled Cases)."""
    scheduled_elective_admissions_24h: int   # Planned OR admissions today
    scheduled_post_op_icu_beds: int          # Surgeries requiring ICU recovery bed
    same_day_surgeries_count: int = 0        # Outpatient/same-day (no bed needed)


class CompleteHospitalSnapshot(BaseModel):
    """Full hospital state snapshot combining all 4 data pillars.

    This is the master input fed into TimesFM for forecasting
    and into the LangGraph agent for reasoning.
    """
    timestamp: str
    hospital_id: str
    census: WardCensusContext
    er_arrivals: ERArrivalsContext
    scheduled_cases: ScheduledCasesContext
    external_signals: list[ExternalSignal] = Field(default_factory=list)
    # 48-hour historical occupancy counts for TimesFM context window
    historical_occupancy_48h: list[int] = Field(default_factory=list)

class ForecastPoint(BaseModel):
    """A single point in the occupancy forecast time series."""

    timestamp: str
    predicted_occupancy: float
    lower_bound: float
    upper_bound: float
    # Explainability: per-driver occupancy contributions (§ interpretability)
    drivers: dict[str, float] | None = None


class ForecastResult(BaseModel):
    """Complete forecast output from the ML model."""
    model_name: str
    model_version: str
    horizon_hours: int
    generated_at: str
    points: list[ForecastPoint]
    confidence: float


class AnomalyResult(BaseModel):
    """Result of anomaly detection on forecast data."""
    detected: bool
    anomaly_type: str | None = None
    severity: Literal["none", "low", "medium", "high", "critical"]
    score: float
    explanation: str
    affected_metric: str | None = None


class Recommendation(BaseModel):
    """An actionable operational recommendation."""
    recommendation_id: str
    title: str
    description: str
    priority: Literal["low", "medium", "high", "critical"]
    rationale: str
    expected_effect: str
    requires_human_approval: bool = True
    confidence: float


class PolicyDecision(BaseModel):
    """Output of the deterministic policy engine."""
    decision: Literal["ALLOW", "HUMAN_APPROVAL", "BLOCK"]
    reason: str
    policy_id: str
    policy_version: str


class AgentResult(BaseModel):
    """Final output of the hospital capacity agent pipeline."""
    request_id: str
    hospital_id: str
    unit_id: str
    objective: str
    data_quality: DataQuality
    hospital_context: HospitalContext
    external_signals: list[ExternalSignal] = Field(default_factory=list)
    forecast: ForecastResult | None = None
    anomaly: AnomalyResult | None = None
    findings: list[str] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    policy_decision: PolicyDecision | None = None
    recommendation_summary: str = ""
    confidence: float = 0.0
