"""Discrete-event simulation engine for hospital floor 3D visualization.

Translates forecast results (single targets or full multi-horizon timeline
playbacks) into micro 3D movement events that Claw3D (React Three Fiber)
renders on the hospital floor.

Layout matches the frontend exactly (page.tsx / HospitalFloor.tsx):
- Admission gate: (-13, 0, 8)   Discharge gate: (13, 0, 8)
- 10 beds: 2 rows of 5 — x in {-10,-5,0,5,10}, z=-5 (Row A) and z=0 (Row B)
- Queue slots: x=-12, z in {8, 6.5, 5}
"""

import asyncio
from datetime import datetime, timezone
from typing import AsyncGenerator

from pydantic import BaseModel


class Position3D(BaseModel):
    """3D world coordinate for the hospital floor."""

    x: float
    y: float
    z: float


class VisualSimulationEvent(BaseModel):
    """A single simulation event to be rendered in the 3D hospital floor."""

    event_id: str
    event_type: str  # PATIENT_ARRIVED, BED_ASSIGNED, STAFF_DISPATCHED, PATIENT_DISCHARGED
    patient_id: str | None = None
    bed_id: str | None = None
    staff_id: str | None = None
    position: Position3D
    target_position: Position3D | None = None  # where the avatar should walk to
    timestamp: str


# ============================================================================
# FLOOR LAYOUT (single source of truth — mirrors frontend page.tsx constants)
# ============================================================================
ADMISSION_GATE = Position3D(x=-13.0, y=0.0, z=8.0)
DISCHARGE_GATE = Position3D(x=13.0, y=0.0, z=8.0)
NURSE_STATION = Position3D(x=0.0, y=0.0, z=3.0)

QUEUE_POSITIONS = [
    Position3D(x=-12.0, y=0.0, z=8.0),
    Position3D(x=-12.0, y=0.0, z=6.5),
    Position3D(x=-12.0, y=0.0, z=5.0),
]

BED_POSITIONS: dict[str, Position3D] = {
    "BED-1": Position3D(x=-10.0, y=0.0, z=-5.0),
    "BED-2": Position3D(x=-5.0, y=0.0, z=-5.0),
    "BED-3": Position3D(x=0.0, y=0.0, z=-5.0),
    "BED-4": Position3D(x=5.0, y=0.0, z=-5.0),
    "BED-5": Position3D(x=10.0, y=0.0, z=-5.0),
    "BED-6": Position3D(x=-10.0, y=0.0, z=0.0),
    "BED-7": Position3D(x=-5.0, y=0.0, z=0.0),
    "BED-8": Position3D(x=0.0, y=0.0, z=0.0),
    "BED-9": Position3D(x=5.0, y=0.0, z=0.0),
    "BED-10": Position3D(x=10.0, y=0.0, z=0.0),
}

NURSE_IDS = ["NURSE-01", "NURSE-02"]

# Bed type mapping for triage matching (10-bed demo floor)
BED_TYPES: dict[str, str] = {
    "BED-1": "ICU",
    "BED-2": "ICU",
    "BED-3": "MED_SURG",
    "BED-4": "MED_SURG",
    "BED-5": "MED_SURG",
    "BED-6": "MED_SURG",
    "BED-7": "TELEMETRY",
    "BED-8": "TELEMETRY",
    "BED-9": "STEP_DOWN",
    "BED-10": "ISOLATION",
}

_CHIEF_COMPLAINTS = [
    "Chest pain, rule out ACS",
    "Shortness of breath, hypoxic",
    "Sepsis secondary to UTI",
    "Altered mental status",
    "GI bleed, hemodynamically stable",
    "Fall with suspected hip fracture",
    "Decompensated heart failure",
    "Diabetic ketoacidosis",
    "Stroke alert, left-sided weakness",
    "Abdominal pain with fever",
]


def generate_surge_boarders(count: int) -> list[dict]:
    """Generates N realistic ER boarder records for fast-track triage.

    Acuity distribution mirrors real ER mixes: mostly ESI 2-3, occasional
    ESI 1 resuscitation, rare ESI 4-5.
    """
    import random

    random.seed()
    boarders: list[dict] = []
    for i in range(count):
        esi = random.choices([1, 2, 3, 4, 5], weights=[5, 30, 45, 15, 5])[0]
        news2 = {
            1: random.randint(9, 15),
            2: random.randint(6, 12),
            3: random.randint(3, 8),
            4: random.randint(1, 4),
            5: random.randint(0, 2),
        }[esi]
        bed_type = random.choices(
            ["ICU", "MED_SURG", "TELEMETRY", "STEP_DOWN", "ISOLATION"],
            weights=[20, 40, 20, 15, 5],
        )[0]
        boarders.append(
            {
                "patient_id": f"ER-{random.randint(1000, 9999)}-{i + 1}",
                "mrn": f"MRN{random.randint(100000, 999999)}",
                "esi_level": esi,
                "news2_score": news2,
                "wait_time_minutes": random.choice([15, 25, 35, 50, 65, 90, 120]),
                "required_bed_type": bed_type,
                "chief_complaint": random.choice(_CHIEF_COMPLAINTS),
                "isolation_required": bed_type == "ISOLATION" or random.random() < 0.08,
            }
        )
    # Sort by true clinical priority so the surge looks realistic on arrival
    from app.models.triage import WaitingPatient
    from app.services.triage_engine import TriageEngine

    boarders.sort(
        key=lambda b: TriageEngine.calculate_priority_score(WaitingPatient(**b)),
        reverse=True,
    )
    return boarders


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HospitalSimulationEngine:
    """Stateful bed-registry simulation of patient flow on the hospital floor.

    Maintains which beds are actually occupied so every transition moves real
    patients between real beds. Produces event sequences with a realistic
    admission/discharge lifecycle:

        Admission:  PATIENT_ARRIVED (gate -> queue slot)
                 -> STAFF_DISPATCHED (nurse walks from station to queue)
                 -> patient walks queue slot -> bed
                 -> BED_ASSIGNED

        Discharge:  STAFF_DISPATCHED (nurse walks station -> bed)
                 -> patient walks bed -> discharge gate
                 -> PATIENT_DISCHARGED (removed once at the gate)
    """

    def __init__(self, total_beds: int = 10, step_delay: float = 1.2) -> None:
        self.total_beds = total_beds
        self.step_delay = step_delay
        # bed_id -> patient_id
        self._bed_registry: dict[str, str] = {}
        # beds that require EVS cleaning before admission
        self._dirty_beds: set[str] = set()
        self._patient_counter = 100
        self._nurse_rotation = 0
        self._event_counter = 0

    # ------------------------------------------------------------------
    # Registry management
    # ------------------------------------------------------------------
    def sync_registry(self, occupied_beds: int) -> None:
        """Resets the registry so that `occupied_beds` beds are marked occupied."""
        self._bed_registry.clear()
        bed_ids = list(BED_POSITIONS.keys())[: self.total_beds]
        for i, bed_id in enumerate(bed_ids):
            if i < occupied_beds:
                self._bed_registry[bed_id] = f"PAT-{i + 1:03d}"

    def free_beds(self) -> list[str]:
        """Bed IDs that are currently unoccupied."""
        return [
            b for b in BED_POSITIONS if b not in self._bed_registry
        ][: self.total_beds]

    def clean_free_beds(self) -> list[str]:
        """Free beds that do NOT need EVS cleaning."""
        return [b for b in self.free_beds() if b not in self._dirty_beds]

    def dirty_free_beds(self) -> list[str]:
        """Free beds that need EVS cleaning first."""
        return [b for b in self.free_beds() if b in self._dirty_beds]

    def mark_dirty(self, bed_id: str) -> None:
        """Flags a bed as needing EVS cleaning (e.g. after a discharge)."""
        self._dirty_beds.add(bed_id)

    def occupied_bed_ids(self) -> dict[str, str]:
        """Snapshot of the current bed_id -> patient_id registry."""
        return dict(self._bed_registry)

    def occupied_count(self) -> int:
        return len(self._bed_registry)

    # ------------------------------------------------------------------
    # Event helpers
    # ------------------------------------------------------------------
    def _next_patient_id(self) -> str:
        self._patient_counter += 1
        return f"PAT-{self._patient_counter:03d}"

    def _next_nurse(self) -> str:
        nurse = NURSE_IDS[self._nurse_rotation % len(NURSE_IDS)]
        self._nurse_rotation += 1
        return nurse

    def _evt(
        self,
        event_type: str,
        position: Position3D,
        target_position: Position3D | None = None,
        **kwargs,
    ) -> VisualSimulationEvent:
        self._event_counter += 1
        return VisualSimulationEvent(
            event_id=f"evt-{self._event_counter:05d}-{event_type.lower()}",
            event_type=event_type,
            position=position,
            target_position=target_position,
            timestamp=_now_iso(),
            **kwargs,
        )

    # ------------------------------------------------------------------
    # Lifecycle generators
    # ------------------------------------------------------------------
    async def _admit_patient(
        self, bed_id: str
    ) -> AsyncGenerator[VisualSimulationEvent, None]:
        """Full admission lifecycle for one patient into a specific free bed."""
        patient_id = self._next_patient_id()
        nurse_id = self._next_nurse()
        bed_pos = BED_POSITIONS[bed_id]

        queue_slot = QUEUE_POSITIONS[
            min(len(QUEUE_POSITIONS) - 1, self._nurse_rotation % len(QUEUE_POSITIONS))
        ]

        # 1. Patient arrives at admission gate, walks into the queue
        yield self._evt(
            "PATIENT_ARRIVED",
            position=ADMISSION_GATE,
            target_position=queue_slot,
            patient_id=patient_id,
        )
        await asyncio.sleep(self.step_delay)

        # 2. Nurse dispatched: walks from station toward the queue slot
        yield self._evt(
            "STAFF_DISPATCHED",
            position=NURSE_STATION,
            target_position=Position3D(x=queue_slot.x + 1.5, y=0.0, z=queue_slot.z - 1.5),
            staff_id=nurse_id,
            patient_id=patient_id,
        )
        await asyncio.sleep(self.step_delay)

        # 3. Patient escorted: walks from queue to the assigned bed
        yield self._evt(
            "PATIENT_ESCORTED",
            position=queue_slot,
            target_position=bed_pos,
            patient_id=patient_id,
            staff_id=nurse_id,
            bed_id=bed_id,
        )
        await asyncio.sleep(self.step_delay)

        # 4. Bed officially assigned; nurse returns toward station
        self._bed_registry[bed_id] = patient_id
        yield self._evt(
            "BED_ASSIGNED",
            position=bed_pos,
            patient_id=patient_id,
            staff_id=nurse_id,
            bed_id=bed_id,
        )
        await asyncio.sleep(self.step_delay * 0.5)

    async def _discharge_patient(
        self, bed_id: str
    ) -> AsyncGenerator[VisualSimulationEvent, None]:
        """Full discharge lifecycle for the patient currently in bed_id."""
        patient_id = self._bed_registry.pop(bed_id, None)
        if not patient_id:
            return
        nurse_id = self._next_nurse()
        bed_pos = BED_POSITIONS[bed_id]

        # 1. Nurse dispatched to collect the patient
        yield self._evt(
            "STAFF_DISPATCHED",
            position=NURSE_STATION,
            target_position=Position3D(x=bed_pos.x, y=0.0, z=bed_pos.z - 1.5),
            staff_id=nurse_id,
            patient_id=patient_id,
        )
        await asyncio.sleep(self.step_delay)

        # 2. Patient walks from the bed toward the discharge gate
        yield self._evt(
            "PATIENT_WALKING_OUT",
            position=bed_pos,
            target_position=DISCHARGE_GATE,
            patient_id=patient_id,
            bed_id=bed_id,
        )
        await asyncio.sleep(self.step_delay)

        # 3. Patient removed once they reach the gate; bed freed but needs EVS cleaning
        self._dirty_beds.add(bed_id)
        yield self._evt(
            "PATIENT_DISCHARGED",
            position=DISCHARGE_GATE,
            patient_id=patient_id,
            bed_id=bed_id,
        )
        await asyncio.sleep(self.step_delay * 0.5)

    async def _evs_clean_bed(
        self, bed_id: str
    ) -> AsyncGenerator[VisualSimulationEvent, None]:
        """EVS team cleans a dirty free bed so it can accept an admission."""
        bed_pos = BED_POSITIONS[bed_id]

        yield self._evt(
            "EVS_CLEANING_STARTED",
            position=bed_pos,
            bed_id=bed_id,
        )
        await asyncio.sleep(self.step_delay)

        self._dirty_beds.discard(bed_id)
        yield self._evt(
            "EVS_CLEANING_COMPLETE",
            position=bed_pos,
            bed_id=bed_id,
        )
        await asyncio.sleep(self.step_delay * 0.3)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    async def generate_simulation_stream(
        self,
        current_occupied: int,
        predicted_occupancy: float,
    ) -> AsyncGenerator[VisualSimulationEvent, None]:
        """Transition the floor from current occupancy to a predicted target.

        Args:
            current_occupied: Currently occupied bed count (syncs the registry).
            predicted_occupancy: Target occupancy rate (0.0 to 1.0).
        """
        self.sync_registry(current_occupied)
        target_beds = int(round(self.total_beds * min(max(predicted_occupancy, 0.0), 1.0)))

        # Discharges first — free up beds before admitting new patients
        while self.occupied_count() > target_beds:
            # discharge the most recently admitted (last dict insertion order)
            bed_to_free = next(reversed(self._bed_registry))
            async for evt in self._discharge_patient(bed_to_free):
                yield evt

        # Then admissions
        while self.occupied_count() < target_beds:
            candidates = self.free_beds()
            if not candidates:
                break
            async for evt in self._admit_patient(candidates[0]):
                yield evt

    async def simulate_forecast_playback(
        self,
        points: list[dict],
        step_delay_multiplier: float = 1.0,
    ) -> AsyncGenerator[tuple[int, int, list[VisualSimulationEvent]], None]:
        """Time-lapse playback across an ordered list of forecast points.

        Each point requires 'time_step_index' and 'predicted_occupied_beds'.

        Yields tuples of (step_index, occupied_after_step, events_batch) so the
        caller can attach per-step status metadata when broadcasting.
        """
        delay = self.step_delay * step_delay_multiplier
        original_delay = self.step_delay
        self.step_delay = max(0.4, min(delay, 2.0))

        try:
            first = points[0]
            start_occ = int(first.get("previous_occupied_beds", first["predicted_occupied_beds"]))
            self.sync_registry(start_occ)

            for point in points:
                step_idx = int(point["time_step_index"])
                target_occ = int(point["predicted_occupied_beds"])
                batch: list[VisualSimulationEvent] = []

                while self.occupied_count() > target_occ:
                    bed_to_free = next(reversed(self._bed_registry))
                    async for evt in self._discharge_patient(bed_to_free):
                        batch.append(evt)

                while self.occupied_count() < target_occ:
                    candidates = self.free_beds()
                    if not candidates:
                        break
                    async for evt in self._admit_patient(candidates[0]):
                        batch.append(evt)

                yield step_idx, self.occupied_count(), batch
        finally:
            self.step_delay = original_delay
