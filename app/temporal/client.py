import asyncio
import uuid

from temporalio.client import Client

from app.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE
from app.models import HospitalRequest
from app.temporal.workflows import HospitalCapacityWorkflow


async def start_workflow() -> None:
    """Start a hospital capacity workflow and simulate human approval."""
    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)

    req_id = f"REQ-{uuid.uuid4().hex[:6]}"
    request = HospitalRequest(
        request_id=req_id,
        hospital_id="HOSPITAL-MAIN-01",
        unit_id="ICU-EAST",
        objective="Predict 24h bed occupancy and evaluate capacity surge risk.",
    )

    workflow_id = f"hospital-capacity-{req_id}"

    print(f"\n[+] Executing Workflow: {workflow_id}")
    print(f"    Request: {request.model_dump_json(indent=2)}")

    # Start workflow
    handle = await client.start_workflow(
        HospitalCapacityWorkflow.run,
        request,
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
    )

    print(f"[+] Workflow started. Waiting for agent execution...")

    # Simulate a human approval signal, but only when policy actually
    # requires it (phase may lag while the agent activity is still running).
    approval_sent = False
    status: dict | None = None
    for _ in range(30):
        await asyncio.sleep(2)
        try:
            status = await handle.query(HospitalCapacityWorkflow.status)
        except Exception:
            continue
        if status.get("phase") == "AWAITING_APPROVAL":
            print(f"[!] Policy requires approval — sending APPROVED=True signal...")
            await handle.signal(HospitalCapacityWorkflow.approve_recommendation, True)
            approval_sent = True
            break
        if status.get("phase") == "COMPLETED":
            print(f"[i] Policy decision was ALLOW — no approval signal needed.")
            break
    if not approval_sent and (status is None or status.get("phase") != "COMPLETED"):
        # Fallback: workflow never reached a known phase within timeout window.
        print(f"[!] Phase unknown after retries — sending approval signal anyway.")
        await handle.signal(HospitalCapacityWorkflow.approve_recommendation, True)

    # Fetch final result
    result = await handle.result()

    print("\n" + "=" * 70)
    print("                    AGENT EXECUTION RESULT")
    print("=" * 70)
    print(f"  Request ID     : {result.request_id}")
    print(f"  Hospital/Unit  : {result.hospital_id} / {result.unit_id}")
    print(f"  Objective      : {result.objective}")
    print(f"  Data Quality   : {result.data_quality.status} (Score: {result.data_quality.quality_score})")
    if result.forecast:
        max_occ = max(p.predicted_occupancy for p in result.forecast.points)
        print(f"  Forecast Model : {result.forecast.model_name} ({result.forecast.horizon_hours}h)")
        print(f"  Max Forecast   : {max_occ:.2%}")
    if result.anomaly:
        print(f"  Anomaly Alert  : {result.anomaly.anomaly_type} [Severity: {result.anomaly.severity}]")
    if result.findings:
        print(f"  Findings       : {len(result.findings)} external alerts")
        for f in result.findings:
            print(f"    • {f}")
    if result.recommendations:
        rec = result.recommendations[0]
        print(f"  Recommendation : {rec.title} (priority={rec.priority})")
        print(f"    Description  : {rec.description[:100]}")
    if result.policy_decision:
        print(f"  Policy Decision: {result.policy_decision.decision}")
        print(f"    Reason       : {result.policy_decision.reason}")
    print(f"  Summary        : {result.recommendation_summary}")
    print(f"  Confidence     : {result.confidence}")
    print("=" * 70 + "\n")


def main() -> None:
    """Entry point for the Temporal client."""
    asyncio.run(start_workflow())


if __name__ == "__main__":
    main()
