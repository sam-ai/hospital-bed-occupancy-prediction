from typing import TypedDict

from app.models import (
    AgentResult,
    AnomalyResult,
    DataQuality,
    ExternalSignal,
    ForecastResult,
    HospitalContext,
    PolicyDecision,
    Recommendation,
)


class HospitalAgentState(TypedDict, total=False):
    """Shared state flowing through the LangGraph hospital agent pipeline."""

    request_id: str
    hospital_id: str
    unit_id: str
    objective: str
    hospital_context: HospitalContext
    data_quality: DataQuality
    external_signals: list[ExternalSignal]
    forecast: ForecastResult
    anomaly: AnomalyResult
    findings: list[str]
    recommendations: list[Recommendation]
    policy_decision: PolicyDecision
    recommendation_summary: str
    confidence: float
    agent_result: AgentResult
