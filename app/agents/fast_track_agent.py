"""LangGraph Fast-Track Admission Agent.

Orchestrates the deterministic triage engine and generates role-specific
multi-channel alert notifications for hospital staff.

Pipeline:
    1. run_triage_matching: Rank patients by ESI/NEWS2/wait and match to beds
    2. generate_role_notifications: Create targeted alerts per staff role/channel
"""

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.models.triage import (
    BedAllocationMatch,
    StaffAlertNotification,
    WaitingPatient,
)
from app.services.triage_engine import TriageEngine

triage_engine = TriageEngine()


class FastTrackAgentState(TypedDict, total=False):
    """State flowing through the fast-track admission agent graph."""

    waiting_patients: list[WaitingPatient]
    available_beds: list[dict[str, Any]]
    pending_discharges: list[dict[str, Any]]
    matches: list[BedAllocationMatch]
    notifications: list[StaffAlertNotification]


async def run_triage_matching(state: FastTrackAgentState) -> dict:
    """Execute deterministic triage scoring and bed matching."""
    matches = triage_engine.rank_and_match_patients(
        waiting_patients=state["waiting_patients"],
        available_beds=state["available_beds"],
        pending_discharges=state["pending_discharges"],
    )
    return {"matches": matches}


async def generate_role_notifications(state: FastTrackAgentState) -> dict:
    """Generate targeted multi-channel notifications for each staff role."""
    matches = state["matches"]
    notifications: list[StaffAlertNotification] = []

    if not matches:
        return {"notifications": notifications}

    top_match = matches[0]
    total_boarders = len(matches)

    # -----------------------------------------------------------------------
    # 1. BED COORDINATOR — Slack critical alert with full triage list
    # -----------------------------------------------------------------------
    notifications.append(
        StaffAlertNotification(
            recipient_role="BED_COORDINATOR",
            channel="SLACK",
            priority="CRITICAL",
            message_title=f"ER Surge Alert: {total_boarders} Boarders Waiting for Beds",
            message_body=(
                f"Triage Priority #1: Patient MRN {top_match.mrn} "
                f"(ESI {top_match.esi_level}, Score {top_match.priority_score}).\n"
                f"Recommended Action: {top_match.action_item}"
            ),
            payload={"matches": [m.model_dump() for m in matches]},
        )
    )

    # -----------------------------------------------------------------------
    # 2. EVS HOUSEKEEPING — SMS for STAT bed cleaning
    # -----------------------------------------------------------------------
    evs_matches = [m for m in matches if m.allocation_status == "AWAITING_EVS_CLEANING"]
    if evs_matches:
        dirty_beds = [m.matched_bed_id for m in evs_matches if m.matched_bed_id]
        notifications.append(
            StaffAlertNotification(
                recipient_role="EVS_HOUSEKEEPING",
                channel="TWILIO_SMS",
                priority="HIGH",
                message_title="STAT Bed Cleaning Required",
                message_body=(
                    f"STAT EVS Cleaning requested for beds: {', '.join(dirty_beds)} "
                    f"to clear ER boarders. {len(evs_matches)} patients waiting."
                ),
                payload={"beds": dirty_beds, "patient_count": len(evs_matches)},
            )
        )

    # -----------------------------------------------------------------------
    # 3. PHYSICIAN — EHR InBasket for expedited discharge review
    # -----------------------------------------------------------------------
    discharge_matches = [m for m in matches if m.allocation_status == "NEEDS_EXPEDITED_DISCHARGE"]
    if discharge_matches:
        discharge_beds = [m.matched_bed_id for m in discharge_matches if m.matched_bed_id]
        notifications.append(
            StaffAlertNotification(
                recipient_role="PHYSICIAN",
                channel="EHR_INBASKET",
                priority="HIGH",
                message_title="Expedited Discharge Review Required",
                message_body=(
                    f"{total_boarders} ER Boarders awaiting beds. "
                    f"Please review pending discharges ({', '.join(discharge_beds)}) "
                    f"to sign off on ready patients."
                ),
                payload={
                    "discharge_beds": discharge_beds,
                    "boarder_count": total_boarders,
                },
            )
        )

    # -----------------------------------------------------------------------
    # 4. NURSING SUPERVISOR — Claw3D 3D UI WebSocket broadcast
    # -----------------------------------------------------------------------
    notifications.append(
        StaffAlertNotification(
            recipient_role="NURSING_SUPERVISOR",
            channel="CLAW3D_UI_WEBSOCKET",
            priority="HIGH",
            message_title="Fast-Track Admission Protocol Activated",
            message_body=(
                f"Fast-track admission protocol triggered for {total_boarders} ER boarders. "
                f"Top priority: {top_match.mrn} (ESI {top_match.esi_level})."
            ),
            payload={
                "action": "TRIGGER_3D_SURGE_ANIMATION",
                "matches": [m.model_dump() for m in matches],
            },
        )
    )

    return {"notifications": notifications}


def build_fast_track_agent():
    """Build the LangGraph fast-track admission agent."""
    builder = StateGraph(FastTrackAgentState)
    builder.add_node("run_triage_matching", run_triage_matching)
    builder.add_node("generate_role_notifications", generate_role_notifications)
    builder.add_edge(START, "run_triage_matching")
    builder.add_edge("run_triage_matching", "generate_role_notifications")
    builder.add_edge("generate_role_notifications", END)
    return builder.compile()


fast_track_agent = build_fast_track_agent()
