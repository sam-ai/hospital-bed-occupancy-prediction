"""End-to-end test of the Fast-Track Admission & Alert Engine.

Simulates 5 ER boarders waiting for beds and executes the full pipeline:
triage scoring → bed matching → multi-channel notification dispatch.
"""

import asyncio

from app.agents.fast_track_agent import fast_track_agent
from app.communications.dispatcher import MultiChannelDispatcher
from app.models.triage import WaitingPatient


async def test_fast_track_admission():
    # Simulate 5 ER Boarders waiting for beds
    waiting_patients = [
        WaitingPatient(
            patient_id="P-101",
            mrn="MRN-8821",
            esi_level=1,
            news2_score=8,
            wait_time_minutes=45,
            required_bed_type="ICU",
            chief_complaint="Acute Respiratory Distress",
        ),
        WaitingPatient(
            patient_id="P-102",
            mrn="MRN-4412",
            esi_level=2,
            news2_score=6,
            wait_time_minutes=90,
            required_bed_type="MED_SURG",
            chief_complaint="Severe Abdominal Pain",
        ),
        WaitingPatient(
            patient_id="P-103",
            mrn="MRN-9931",
            esi_level=2,
            news2_score=7,
            wait_time_minutes=120,
            required_bed_type="TELEMETRY",
            chief_complaint="Chest Pain / Angina",
        ),
        WaitingPatient(
            patient_id="P-104",
            mrn="MRN-1102",
            esi_level=3,
            news2_score=3,
            wait_time_minutes=180,
            required_bed_type="MED_SURG",
            chief_complaint="Dehydration / Fever",
        ),
        WaitingPatient(
            patient_id="P-105",
            mrn="MRN-5520",
            esi_level=3,
            news2_score=2,
            wait_time_minutes=60,
            required_bed_type="MED_SURG",
            chief_complaint="Cellulitis",
        ),
    ]

    # Available beds in the hospital
    available_beds = [
        {"bed_id": "BED-02", "type": "ICU", "status": "CLEAN"},
        {"bed_id": "BED-05", "type": "MED_SURG", "status": "DIRTY"},
    ]

    # Pending discharges today
    pending_discharges = [
        {"bed_id": "BED-08", "type": "TELEMETRY", "status": "PENDING_DISCHARGE"},
        {"bed_id": "BED-09", "type": "MED_SURG", "status": "PENDING_DISCHARGE"},
    ]

    print("=" * 74)
    print("   EXECUTING FAST-TRACK ADMISSION & ALERT AGENT FOR 5 ER BOARDERS")
    print("=" * 74)

    # 1. Run LangGraph Agent
    state = {
        "waiting_patients": waiting_patients,
        "available_beds": available_beds,
        "pending_discharges": pending_discharges,
    }
    result = await fast_track_agent.ainvoke(state)

    # 2. Verify and display triage matches
    matches = result["matches"]
    assert len(matches) == 5, f"Expected 5 matches, got {len(matches)}"

    print("\n--- Triage Rank & Bed Allocation Matches ---")
    for idx, match in enumerate(matches):
        print(
            f"Rank #{idx + 1} | MRN: {match.mrn} | ESI: {match.esi_level} "
            f"| Score: {match.priority_score}"
        )
        print(f"  -> Bed: {match.matched_bed_id or 'None'} | Status: {match.allocation_status}")
        print(f"  -> Action: {match.action_item}")
        print()

    # Verify priority ordering (ESI 1 should be first)
    assert matches[0].esi_level == 1, "ESI 1 patient should be highest priority"
    assert matches[0].allocation_status == "READY_TO_ASSIGN", "ICU bed should be assigned"
    assert matches[0].matched_bed_id == "BED-02"

    # 3. Dispatch notifications
    notifications = result["notifications"]
    assert len(notifications) >= 3, f"Expected at least 3 notifications, got {len(notifications)}"

    print("--- Multi-Channel Staff Alerts Broadcast ---")
    dispatcher = MultiChannelDispatcher()
    dispatch_results = await dispatcher.dispatch_notifications(notifications)

    # Verify all dispatched
    assert all(r["status"] in ("SENT", "BROADCASTED", "DELIVERED") for r in dispatch_results)

    # Verify notification types
    channels_used = [n.channel for n in notifications]
    assert "SLACK" in channels_used
    assert "CLAW3D_UI_WEBSOCKET" in channels_used

    print("=" * 74)
    print("[SUCCESS] Fast-Track Admission & Alert Engine verified!")
    print(f"  - {len(matches)} patients triaged and matched")
    print(f"  - {len(notifications)} notifications dispatched across {len(set(channels_used))} channels")
    print("=" * 74)


if __name__ == "__main__":
    asyncio.run(test_fast_track_admission())
