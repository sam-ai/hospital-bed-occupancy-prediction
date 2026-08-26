from datetime import datetime, timezone

from temporalio import activity

from app.agents.hospital_graph import hospital_agent_graph
from app.communications.dispatcher import MultiChannelDispatcher
from app.integrations.briefing_agent import CapacityBriefing, generate_capacity_briefing
from app.models import AgentResult, ExecutionReport, HospitalRequest
from app.models.triage import StaffAlertNotification

dispatcher = MultiChannelDispatcher()

_PRIORITY_MAP = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "low": "LOW",
}


def build_staff_alerts(result: AgentResult) -> list[StaffAlertNotification]:
    """Map approved recommendations into staff alert notifications.

    Currently only CLAW3D_UI_WEBSOCKET channel is dispatched; SLACK,
    TWILIO_SMS, and EHR_INBASKET channels can be added here later.
    """
    notifications: list[StaffAlertNotification] = []
    for rec in result.recommendations:
        priority = _PRIORITY_MAP.get(rec.priority, "MEDIUM")
        role = "NURSING_SUPERVISOR" if rec.priority == "critical" else "BED_COORDINATOR"
        notifications.append(
            StaffAlertNotification(
                recipient_role=role,
                channel="CLAW3D_UI_WEBSOCKET",
                priority=priority,
                message_title=rec.title,
                message_body=rec.description,
                payload={
                    "action": "SURGE_ESCALATION",
                    "recommendation_id": rec.recommendation_id,
                    "hospital_id": result.hospital_id,
                    "unit_id": result.unit_id,
                    "priority": priority,
                },
            )
        )
    return notifications


@activity.defn
async def execute_approved_recommendation(
    result: AgentResult, approved: bool
) -> ExecutionReport:
    """Execute an approved recommendation by dispatching staff alerts."""
    if not approved:
        return ExecutionReport(
            status="REJECTED", dispatched_at=datetime.now(timezone.utc).isoformat()
        )

    alerts = build_staff_alerts(result)
    sent = await dispatcher.dispatch_notifications(alerts)

    activity.logger.info(
        "Executed approved recommendations for %s/%s: %d notifications dispatched",
        result.hospital_id,
        result.unit_id,
        len(sent),
    )
    return ExecutionReport(
        status="EXECUTED",
        dispatched_at=datetime.now(timezone.utc).isoformat(),
        notifications_sent=sent,
    )


@activity.defn
async def generate_daily_briefing(result: AgentResult) -> CapacityBriefing:
    """Call the AAVA Daily Hospital Capacity Briefing Agent for this result."""
    activity.logger.info(
        "Requesting AAVA capacity briefing for %s/%s", result.hospital_id, result.unit_id
    )
    briefing = await generate_capacity_briefing(result)
    activity.logger.info(
        "Briefing received: riskLevel=%s requiresAttention=%s",
        briefing.riskLevel,
        briefing.requiresAttention,
    )
    return briefing


@activity.defn
async def run_agent(request: HospitalRequest) -> AgentResult:
    """Temporal activity that executes the full LangGraph hospital agent pipeline."""
    activity.logger.info(
        "Executing Agent Activity for Request: %s", request.request_id
    )

    initial_state = {
        "request_id": request.request_id,
        "hospital_id": request.hospital_id,
        "unit_id": request.unit_id,
        "objective": request.objective,
    }

    result = await hospital_agent_graph.ainvoke(initial_state)
    return result["agent_result"]
