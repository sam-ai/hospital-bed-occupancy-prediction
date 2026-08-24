"""FastAPI WebSocket & REST gateway bridging Temporal/Agent backend to Claw3D frontend.

Responsibilities:
- WebSocket endpoint for real-time 3D simulation event streaming
- REST endpoint to trigger Temporal capacity check workflows
- Human approval signal relay from frontend to Temporal workflow
"""

import asyncio
import json
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.api.timeline_router import router as forecast_timeline_router
from app.agents.fast_track_agent import fast_track_agent
from app.communications.dispatcher import MultiChannelDispatcher
from app.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE
from app.data.elasticsearch_client import FORECAST_INDEX, es_client
from app.models import HospitalRequest
from app.models.triage import WaitingPatient
from app.simulation.engine import (
    BED_TYPES,
    HospitalSimulationEngine,
    generate_surge_boarders,
)
from app.temporal.scheduled_workflow import DailyForecastWorkflow

app = FastAPI(
    title="Hospital AI Agent Gateway",
    description="Real-time bridge between Hospital AI Agent and Claw3D 3D visualization",
    version="0.1.0",
)

app.include_router(forecast_timeline_router)


_temporal_client = None
_temporal_client_lock: asyncio.Lock | None = None


async def _get_temporal_client():
    """Return a shared Temporal client, connecting lazily on first use."""
    global _temporal_client, _temporal_client_lock
    if _temporal_client is not None:
        return _temporal_client
    if _temporal_client_lock is None:
        _temporal_client_lock = asyncio.Lock()
    async with _temporal_client_lock:
        if _temporal_client is None:
            from temporalio.client import Client

            _temporal_client = await Client.connect(
                TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE
            )
    return _temporal_client

# Allow Claw3D frontend (Next.js dev server) to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConnectionManager:
    """Manage active WebSocket connections for broadcasting simulation events."""

    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        """Send a JSON message to all connected clients."""
        disconnected: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.append(connection)
        for conn in disconnected:
            self.disconnect(conn)


manager = ConnectionManager()
sim_engine = HospitalSimulationEngine(total_beds=10)
playback_lock = asyncio.Lock()
_active_playback_task: asyncio.Task | None = None
# The 3D demo floor has 10 beds while forecasts target the real unit size,
# so absolute bed counts are rescaled to scene proportions.
SCENE_TOTAL_BEDS = 10


def _scale_points_to_scene(points: list[dict]) -> list[dict]:
    """Converts absolute predicted_occupied_beds into 10-bed-scene counts."""
    scaled = []
    for p in points:
        q = dict(p)
        pred_occ = float(p.get("predicted_occupancy", 0.0))
        q["predicted_occupied_beds"] = int(round(pred_occ * SCENE_TOTAL_BEDS))
        scaled.append(q)
    return scaled


# ============================================================================
# FORECAST-DRIVEN SIMULATION HELPERS
# ============================================================================
async def _fetch_forecast_points(
    horizon_type: str,
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
) -> list[dict]:
    """Fetches today's (or the latest) forecast points for a horizon from ES."""
    from datetime import datetime, timezone

    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_filters = [
        {"term": {"hospital_id": hospital_id}},
        {"term": {"unit_id": unit_id}},
        {"term": {"horizon_type": horizon_type}},
    ]

    async def _search(forecast_date: str | None) -> list[dict]:
        must = list(base_filters)
        if forecast_date:
            must.append({"term": {"forecast_date": forecast_date}})
        response = await es_client.search(
            index=FORECAST_INDEX,
            body={
                "size": 200,
                "query": {"bool": {"must": must}},
                "sort": [{"time_step_index": {"order": "asc"}}],
            },
        )
        return [hit["_source"] for hit in response["hits"]["hits"]]

    points = await _search(today_str)
    if not points:
        points = await _search(None)
    return points


async def _broadcast_events(events, extra: dict | None = None) -> None:
    for evt in events:
        message = {"type": "SIMULATION_3D_EVENT", "event": evt.model_dump()}
        if extra:
            message.update(extra)
        await manager.broadcast(message)


async def _run_playback_step(data: dict) -> None:
    """Transitions the floor from previous step to the selected forecast step."""
    global _active_playback_task

    horizon_type = data.get("horizon_type", "24H")
    step_index = int(data.get("step_index", 1))
    speed = max(0.25, min(float(data.get("speed", 1.0)), 4.0))

    async with playback_lock:
        points = await _fetch_forecast_points(horizon_type)
        if not points:
            await manager.broadcast({
                "type": "SIM_PLAYBACK_ERROR",
                "message": f"No {horizon_type} forecast available. Run the scheduled workflow first.",
            })
            return

        points = _scale_points_to_scene(points)
        selected = next((p for p in points if int(p["time_step_index"]) == step_index), None)
        if not selected:
            await manager.broadcast({
                "type": "SIM_PLAYBACK_ERROR",
                "message": f"Step {step_index} not found in {horizon_type} forecast.",
            })
            return

        idx = points.index(selected)
        prev_occ = (
            int(round(float(points[idx - 1]["predicted_occupancy"]) * SCENE_TOTAL_BEDS)) if idx > 0
            else int(round(float(selected["predicted_occupancy"]) * SCENE_TOTAL_BEDS))
        )
        target_occ = int(selected["predicted_occupied_beds"])  # already scaled

        await manager.broadcast({
            "type": "SIM_PLAYBACK_STARTED",
            "mode": "STEP",
            "horizon_type": horizon_type,
            "step_index": step_index,
        })

        original_delay = sim_engine.step_delay
        sim_engine.step_delay = max(0.2, original_delay / speed)
        try:
            aborted = False
            sim_engine.sync_registry(prev_occ)
            async for batch_events in _transition_generator(prev_occ, target_occ):
                await _broadcast_events(batch_events)
                await manager.broadcast({
                    "type": "SIM_PLAYBACK_STATUS",
                    "mode": "STEP",
                    "horizon_type": horizon_type,
                    "step_index": step_index,
                    "occupied_beds": sim_engine.occupied_count(),
                })
                if _active_playback_task is not None and _active_playback_task.cancelled():
                    aborted = True
                    break
            if not aborted:
                sim_engine.sync_registry(target_occ)

            await manager.broadcast({
                "type": "SIM_PLAYBACK_COMPLETE",
                "horizon_type": horizon_type,
                "step_index": step_index,
                "occupied_beds": sim_engine.occupied_count(),
                "aborted": aborted,
            })
        finally:
            sim_engine.step_delay = original_delay


async def _run_fast_track_admission(data: dict) -> None:
    """Runs the Fast-Track Admission Agent on ER boarders and animates results.

    Pipeline:
    1. Build live bed inventory (CLEAN free / DIRTY free / pending discharges)
    2. Invoke the LangGraph fast-track agent for triage matching + notifications
    3. Dispatch notifications through multi-channel dispatcher
    4. Broadcast FAST_TRACK_RESULT to the UI
    5. Animate admissions in clinical priority order on the 3D floor
    """
    boarders_raw = data.get("boarders") or []
    try:
        waiting_patients = [WaitingPatient(**b) for b in boarders_raw]
    except Exception as e:
        await manager.broadcast({
            "type": "FAST_TRACK_ERROR",
            "message": f"Invalid boarder payload: {e}",
        })
        return

    if not waiting_patients:
        await manager.broadcast({
            "type": "FAST_TRACK_ERROR",
            "message": "No ER boarders provided.",
        })
        return

    # ---- 1. Live bed inventory from the simulation registry ----
    occupied = sim_engine.occupied_bed_ids()
    available_beds = [
        {
            "bed_id": bed_id,
            "type": BED_TYPES.get(bed_id, "MED_SURG"),
            "status": "DIRTY" if bed_id in {b for b in sim_engine.dirty_free_beds()} else "CLEAN",
        }
        for bed_id in sim_engine.free_beds()
    ]
    pending_discharges = [
        {"bed_id": bed_id, "type": BED_TYPES.get(bed_id, "MED_SURG")}
        for bed_id in occupied
    ]

    # ---- 2. LangGraph fast-track agent (streamed per node) ----
    agent_input = {
        "waiting_patients": waiting_patients,
        "available_beds": available_beds,
        "pending_discharges": pending_discharges,
    }

    run_id = datetime.now(timezone.utc).strftime("%H%M%S%f")[:8]
    matches: list = []
    notifications: list = []

    await manager.broadcast({
        "type": "AGENT_STAGE",
        "run_id": run_id,
        "stage": "TRIAGE_MATCHING",
        "status": "RUNNING",
        "detail": f"Scoring {len(waiting_patients)} boarders against {len(available_beds)} open beds…",
    })

    async for update in fast_track_agent.astream(agent_input):
        # astream yields {node_name: node_output} per completed node
        node_out = next(iter(update.values())) if isinstance(update, dict) else None
        if not isinstance(node_out, dict):
            continue
        if "matches" in node_out:
            matches = node_out["matches"]
            await manager.broadcast({
                "type": "AGENT_STAGE",
                "run_id": run_id,
                "stage": "TRIAGE_MATCHING",
                "status": "DONE",
                "detail": (
                    f"{len(waiting_patients)} boarders scored · "
                    f"{sum(1 for m in matches if m.matched_bed_id)} beds matched"
                ),
            })
        elif "notifications" in node_out:
            notifications = node_out["notifications"]
            await manager.broadcast({
                "type": "AGENT_STAGE",
                "run_id": run_id,
                "stage": "ROLE_NOTIFICATIONS",
                "status": "DONE",
                "detail": f"{len(notifications)} staff alerts composed",
            })

    # ---- 3. Multi-channel dispatch (individual receipts) ----
    await manager.broadcast({
        "type": "AGENT_STAGE",
        "run_id": run_id,
        "stage": "CHANNEL_DISPATCH",
        "status": "RUNNING",
        "detail": f"Dispatching {len(notifications)} alerts…",
    })
    dispatcher = MultiChannelDispatcher()
    dispatch_results = []
    for note in notifications:
        res = (await dispatcher.dispatch_notifications([note]))[0]
        dispatch_results.append(res)
        await manager.broadcast({
            "type": "AGENT_DISPATCH",
            "run_id": run_id,
            "channel": note.channel,
            "recipient_role": note.recipient_role,
            "priority": note.priority,
            "message_title": note.message_title,
            "status": res.get("status", "UNKNOWN"),
        })

    # ---- 4. Broadcast structured result to the UI ----
    # Enrich matches with LOS predictions (guide §3.3)
    from app.services.los_model import predict_los

    patient_by_id = {wp.patient_id: wp for wp in waiting_patients}
    enriched = []
    for m in matches:
        md = m.model_dump()
        wp = patient_by_id.get(m.patient_id)
        los = predict_los({
            "esi_level": m.esi_level,
            "required_bed_type": wp.required_bed_type if wp else "MED_SURG",
            "isolation_required": wp.isolation_required if wp else False,
        })
        md["predicted_los_hours"] = los["predicted_los_hours"] if los else None
        md["los_top_factors"] = los["top_factors"] if los else None
        enriched.append(md)

    await manager.broadcast({
        "type": "FAST_TRACK_RESULT",
        "run_id": run_id,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "matches": enriched,
        "notifications": [n.model_dump() for n in notifications],
        "dispatch_results": dispatch_results,
        "total_boarders": len(waiting_patients),
    })

    # ---- 5. Animate floor transitions in priority order ----
    async with playback_lock:
        for match in matches:
            bed_id = match.matched_bed_id
            status = match.allocation_status
            if not bed_id:
                continue

            if status == "NEEDS_EXPEDITED_DISCHARGE" and bed_id in sim_engine.occupied_bed_ids():
                async for evt in sim_engine._discharge_patient(bed_id):
                    await _broadcast_events([evt])

            if status == "AWAITING_EVS_CLEANING" and bed_id in sim_engine.dirty_free_beds():
                async for evt in sim_engine._evs_clean_bed(bed_id):
                    await _broadcast_events([evt])

            if bed_id in sim_engine.clean_free_beds() or (
                bed_id in sim_engine.free_beds()
                and bed_id not in sim_engine.dirty_free_beds()
            ):
                async for evt in sim_engine._admit_patient(bed_id):
                    await _broadcast_events([evt])

        await manager.broadcast({
            "type": "FAST_TRACK_ANIMATION_COMPLETE",
            "occupied_beds": sim_engine.occupied_count(),
            "admitted": sum(
                1 for m in matches if m.allocation_status == "READY_TO_ASSIGN"
            ),
        })


# ============================================================================
# MOCK DATA REGIME SWITCHER (demo interactivity)
# ============================================================================
class MockRegimeBody(BaseModel):
    scenario: str = "balanced"
    days: int = 30
    seed: int | None = None
    trigger_forecast: bool = True


async def regenerate_mock_regime(body: MockRegimeBody) -> dict:
    """Generates new regime data, ingests into ES, optionally refreshes forecasts."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))
    from app.data.mock_regimes import SCENARIOS, generate_scenario_data

    if body.scenario not in SCENARIOS:
        return {"status": "ERROR", "message": f"Unknown scenario '{body.scenario}'. Options: {list(SCENARIOS)}"}

    dataset = generate_scenario_data(
        scenario=body.scenario, days=body.days, seed=body.seed
    )

    # Also emit patient-stay records for the LOS prediction model
    try:
        from app.data.mock_regimes import generate_patient_stays

        stays = generate_patient_stays(dataset)
        data_dir = Path(__file__).parent.parent / "data"
        data_dir.mkdir(exist_ok=True)
        (data_dir / "patient_stays.json").write_text(json.dumps(stays, indent=2))
        # Invalidate the cached LOS model so it retrains on the new regime
        try:
            from app.services import los_model

            los_model.get_los_model.cache_clear()
            los_model._load_training_data.cache_clear()
        except Exception:
            pass
    except Exception as e:
        print(f"[!] Stay generation skipped ({e})")

    # Purge the unit's previous snapshots so regimes replace cleanly
    await es_client.delete_by_query(
        index="hospital-snapshots",
        body={
            "query": {
                "bool": {
                    "must": [
                        {"term": {"hospital_id": "HOSPITAL-MAIN-01"}},
                        {"term": {"census.unit_id": "FLOOR-1"}},
                    ]
                }
            }
        },
        conflicts="proceed",
    )

    from elasticsearch.helpers import async_bulk

    # Deterministic doc IDs mirror the ingest script so old-regime docs are overwritten
    def _doc_id(s: dict) -> str:
        return (
            f"{s.get('hospital_id', 'UNKNOWN')}_"
            f"{s.get('census', {}).get('unit_id', 'UNKNOWN')}_"
            f"{s.get('timestamp', 'NO_TS').replace(':', '-').replace('.', '-')}"
        )

    actions = [
        {"_index": "hospital-snapshots", "_id": _doc_id(s), "_source": s}
        for s in (snapshot.model_dump() for snapshot in dataset)
    ]
    success, _ = await async_bulk(es_client, actions)

    workflow_id = None
    if body.trigger_forecast:
        try:
            from temporalio.client import Client

            client = await Client.connect(
                TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE
            )
            workflow_id = f"daily-forecast-regime-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
            await client.start_workflow(
                DailyForecastWorkflow.run,
                id=workflow_id,
                task_queue=TEMPORAL_TASK_QUEUE,
            )
        except Exception as e:
            print(f"[!] Forecast re-trigger failed ({e})")

    await manager.broadcast({
        "type": "MOCK_REGIME_CHANGED",
        "scenario": body.scenario,
        "records_ingested": success,
        "forecast_workflow_id": workflow_id,
    })

    return {
        "status": "SUCCESS",
        "scenario": body.scenario,
        "records_ingested": success,
        "forecast_workflow_id": workflow_id,
    }


async def _transition_generator(current: int, target: int):
    """Yields event batches transitioning occupancy current -> target."""
    sim_engine.sync_registry(current)
    while sim_engine.occupied_count() > target:
        bed_to_free = next(reversed(sim_engine._bed_registry))
        batch = []
        async for evt in sim_engine._discharge_patient(bed_to_free):
            batch.append(evt)
        yield batch
    while sim_engine.occupied_count() < target:
        candidates = sim_engine.free_beds()
        if not candidates:
            break
        batch = []
        async for evt in sim_engine._admit_patient(candidates[0]):
            batch.append(evt)
        yield batch


async def _run_playback_timeline(data: dict) -> None:
    """Time-lapse playback across all steps of a horizon's forecast."""
    horizon_type = data.get("horizon_type", "24H")
    speed = max(0.25, min(float(data.get("speed", 1.0)), 4.0))

    if playback_lock.locked():
        await manager.broadcast({
            "type": "SIM_PLAYBACK_ERROR",
            "message": "A simulation playback is already running.",
        })
        return

    aborted = False
    async with playback_lock:
        points = await _fetch_forecast_points(horizon_type)
        if not points:
            await manager.broadcast({
                "type": "SIM_PLAYBACK_ERROR",
                "message": f"No {horizon_type} forecast available. Run the scheduled workflow first.",
            })
            return
        points = _scale_points_to_scene(points)

        await manager.broadcast({
            "type": "SIM_PLAYBACK_STARTED",
            "mode": "TIMELINE",
            "horizon_type": horizon_type,
            "total_steps": len(points),
            "speed": speed,
        })

        try:
            async for step_idx, occupied_after, batch in sim_engine.simulate_forecast_playback(
                points, step_delay_multiplier=1.0 / speed
            ):
                await _broadcast_events(batch, extra={
                    "playback_status": {
                        "mode": "TIMELINE",
                        "horizon_type": horizon_type,
                        "step_index": step_idx,
                        "occupied_beds": occupied_after,
                    },
                })
                await manager.broadcast({
                    "type": "SIM_PLAYBACK_STATUS",
                    "horizon_type": horizon_type,
                    "step_index": step_idx,
                    "occupied_beds": occupied_after,
                })
                if _active_playback_task is not None and _active_playback_task.cancelled():
                    aborted = True
                    break
        except asyncio.CancelledError:
            aborted = True

        final_occ = sim_engine.occupied_count()
        last_step = int(points[-1]["time_step_index"])
        await manager.broadcast({
            "type": "SIM_PLAYBACK_COMPLETE",
            "horizon_type": horizon_type,
            "step_index": last_step,
            "occupied_beds": final_occ,
            "aborted": aborted,
        })


@app.websocket("/ws/simulation")
async def websocket_simulation_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for bidirectional communication with Claw3D frontend.

    Receives:
        - SUBMIT_HUMAN_APPROVAL: Relays approval/rejection to Temporal workflow
        - REQUEST_SIMULATION: Triggers a standalone simulation stream

    Sends:
        - SIMULATION_3D_EVENT: Real-time patient movement events
        - REQUIRE_HUMAN_APPROVAL: Policy gate triggered
        - AGENT_RESULT_READY: Full agent result available
        - APPROVAL_ACKNOWLEDGED: Confirmation of approval relay
    """
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()

            action = data.get("action", "")

            if action == "SUBMIT_HUMAN_APPROVAL":
                # Relay human approval signal to Temporal workflow
                workflow_id = data.get("workflow_id", "")
                approved = data.get("approved", False)

                try:
                    from app.temporal.workflows import HospitalCapacityWorkflow

                    client = await _get_temporal_client()
                    handle = client.get_workflow_handle(workflow_id)
                    await handle.signal(
                        HospitalCapacityWorkflow.approve_recommendation, approved
                    )
                    await websocket.send_json({
                        "type": "APPROVAL_ACKNOWLEDGED",
                        "workflow_id": workflow_id,
                        "approved": approved,
                    })
                except Exception as e:
                    await websocket.send_json({
                        "type": "ERROR",
                        "message": f"Failed to signal workflow: {e}",
                    })

            elif action == "PLAY_FORECAST_STEP":
                # Transition the floor to a specific forecast step from ES
                _active_playback_task = asyncio.create_task(_run_playback_step(data))

            elif action == "PLAY_FORECAST_TIMELINE":
                # Animated time-lapse across the full horizon
                _active_playback_task = asyncio.create_task(_run_playback_timeline(data))

            elif action == "STOP_PLAYBACK":
                if (
                    _active_playback_task is not None
                    and not _active_playback_task.done()
                ):
                    _active_playback_task.cancel()
                else:
                    await manager.broadcast({
                        "type": "SIM_PLAYBACK_COMPLETE",
                        "aborted": True,
                        "occupied_beds": sim_engine.occupied_count(),
                    })

            elif action == "RUN_FAST_TRACK_ADMISSION":
                _active_playback_task = asyncio.create_task(_run_fast_track_admission(data))

            elif action == "SIMULATE_ER_SURGE":
                count = int(data.get("count", 4))
                data["boarders"] = generate_surge_boarders(count)
                _active_playback_task = asyncio.create_task(_run_fast_track_admission(data))

    except WebSocketDisconnect:
        manager.disconnect(websocket)


@app.post("/api/trigger-capacity-check")
async def trigger_capacity_check(request_data: HospitalRequest) -> dict:
    """Trigger the full Temporal workflow and stream results to connected Claw3D clients.

    1. Starts HospitalCapacityWorkflow in Temporal
    2. Broadcasts workflow status to all connected WebSocket clients
    3. Monitors workflow completion and streams simulation events
    """
    from app.temporal.workflows import HospitalCapacityWorkflow

    client = await _get_temporal_client()
    workflow_id = f"hospital-capacity-{request_data.request_id}"

    # Start Temporal Workflow
    handle = await client.start_workflow(
        HospitalCapacityWorkflow.run,
        request_data,
        id=workflow_id,
        task_queue=TEMPORAL_TASK_QUEUE,
    )

    # Broadcast workflow started to Claw3D UI
    await manager.broadcast({
        "type": "WORKFLOW_STARTED",
        "workflow_id": workflow_id,
        "request_id": request_data.request_id,
    })

    # Monitor workflow in background and stream results
    asyncio.create_task(_monitor_and_stream(handle, workflow_id))

    return {"status": "TRIGGERED", "workflow_id": workflow_id}


@app.post("/api/mock/regenerate")
async def post_mock_regenerate(body: MockRegimeBody) -> dict:
    """Switches the demo data regime (balanced/high_capacity/volatile/recovery)."""
    return await regenerate_mock_regime(body)


@app.post("/api/run-simulation-only")
async def run_simulation_only(
    current_occupied: int = 6,
    predicted_occupancy: float = 0.9,
) -> dict:
    """Run simulation without Temporal (for demo/testing without full backend).

    Streams 3D events directly to connected WebSocket clients.
    """
    asyncio.create_task(
        _stream_simulation(current_occupied, predicted_occupancy)
    )
    return {
        "status": "STREAMING",
        "current_occupied": current_occupied,
        "predicted_occupancy": predicted_occupancy,
    }


async def _stream_simulation(current_occupied: int, predicted_occupancy: float) -> None:
    """Stream simulation events to all connected WebSocket clients."""
    async for sim_event in sim_engine.generate_simulation_stream(
        current_occupied=current_occupied,
        predicted_occupancy=predicted_occupancy,
    ):
        await manager.broadcast({
            "type": "SIMULATION_3D_EVENT",
            "event": sim_event.model_dump(),
        })


async def _monitor_and_stream(handle, workflow_id: str) -> None:
    """Monitor a Temporal workflow and stream results + simulation to frontend."""
    try:
        from app.temporal.workflows import HospitalCapacityWorkflow

        # Poll workflow phase so the UI gets the approval prompt *before*
        # the workflow blocks waiting for the human signal.
        approval_notified = False
        while not approval_notified:
            try:
                status = await handle.query(HospitalCapacityWorkflow.status)
            except Exception:
                status = None

            if status and status.get("phase") == "AWAITING_APPROVAL":
                await manager.broadcast({
                    "type": "REQUIRE_HUMAN_APPROVAL",
                    "workflow_id": workflow_id,
                    "recommendations": status.get("recommendations", []),
                })
                approval_notified = True
                break
            if status and status.get("phase") == "COMPLETED":
                break
            await asyncio.sleep(2)

        result = await handle.result()

        # Broadcast full agent result
        await manager.broadcast({
            "type": "AGENT_RESULT_READY",
            "workflow_id": workflow_id,
            "data": result.model_dump(),
        })

        # Notify UI that approved recommendations were executed (staff alerts)
        if (
            result.execution_report
            and result.execution_report.status == "EXECUTED"
        ):
            from app.temporal.activities import build_staff_alerts

            await manager.broadcast({
                "type": "RECOMMENDATION_EXECUTED",
                "workflow_id": workflow_id,
                "notifications": [
                    {
                        "recipient_role": n.recipient_role,
                        "channel": n.channel,
                        "priority": n.priority,
                        "message_title": n.message_title,
                        "message_body": n.message_body,
                    }
                    for n in build_staff_alerts(result)
                ],
            })

        # Stream 3D simulation events based on forecast
        if result.forecast and result.hospital_context:
            max_pred = max(p.predicted_occupancy for p in result.forecast.points)
            async for sim_event in sim_engine.generate_simulation_stream(
                current_occupied=result.hospital_context.occupied_beds,
                predicted_occupancy=max_pred,
            ):
                await manager.broadcast({
                    "type": "SIMULATION_3D_EVENT",
                    "event": sim_event.model_dump(),
                })
    except Exception as e:
        await manager.broadcast({
            "type": "ERROR",
            "workflow_id": workflow_id,
            "message": str(e),
        })


@app.get("/api/health")
async def health_check() -> dict:
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": "Hospital AI Agent Gateway",
        "connected_clients": len(manager.active_connections),
    }
