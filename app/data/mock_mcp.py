"""Mock MCP (Model Context Protocol) clients for hospital EMR and public health data.

Provides both the basic HospitalContext (for backward compatibility with the
existing agent pipeline) and the expanded CompleteHospitalSnapshot (for
advanced 4-pillar forecasting).

In production, these would connect to hospital ADT systems, bed management,
ER tracking, OR scheduling, and public epidemiological surveillance APIs.
"""

import json
import math
import random
from datetime import datetime, timezone
from pathlib import Path

from app.models import (
    CompleteHospitalSnapshot,
    ERArrivalsContext,
    ExternalSignal,
    HospitalContext,
    ScheduledCasesContext,
    WardCensusContext,
)
from app.data.wards import get_ward


class HospitalMCPClient:
    """Mock client simulating Model Context Protocol (MCP) tool integration with EMR/EHR.

    Provides:
    - get_hospital_context(): Basic context (backward compat for agent pipeline)
    - get_complete_snapshot(): Full 4-pillar snapshot for advanced forecasting

    Both are ward-aware: returned data reflects the requested unit_id.
    """

    def __init__(self) -> None:
        # Try to load pre-generated multi-ward 30-day data
        self._mock_data: list[dict] | None = None
        self._data_indexes: dict[str, int] = {}
        data_file = Path(__file__).parent.parent.parent / "data" / "hospital_30day_mock_data.json"
        if data_file.exists():
            try:
                self._mock_data = json.loads(data_file.read_text())
            except Exception:
                pass

    def _ward_records(self, unit_id: str) -> list[dict]:
        """All 30-day records belonging to one ward, in chronological order."""
        if not self._mock_data:
            return []
        return [
            r for r in self._mock_data
            if r.get("census", {}).get("unit_id") == unit_id
        ]

    async def get_hospital_context(self, hospital_id: str, unit_id: str) -> HospitalContext:
        """Retrieve current bed, staffing state, and 48-hour occupancy history.

        Returns the basic HospitalContext used by the existing agent pipeline.
        """
        ward = get_ward(unit_id)
        total_beds = ward.total_beds

        # Prefer this ward's pre-generated 30-day series (latest record)
        records = self._ward_records(ward.unit_id)
        if records:
            latest = records[-1]
            census = latest["census"]
            return HospitalContext(
                hospital_id=hospital_id,
                unit_id=ward.unit_id,
                total_beds=census["total_beds"],
                occupied_beds=census["occupied_beds"],
                admissions_24h=census["admissions_24h"],
                discharges_24h=census["discharges_24h"],
                staff_on_duty=census["staff_on_duty"],
                average_los_hours=census["average_los_hours"],
                timestamp=latest["timestamp"],
                historical_occupancy_counts=list(latest["historical_occupancy_48h"]),
            )

        # Fallback: deterministic synthetic series from the ward profile
        random.seed(42)
        occ_floor = int(total_beds * ward.occupancy_floor_frac)
        occ_ceiling = int(total_beds * ward.occupancy_ceiling_frac)
        base = (occ_floor + occ_ceiling) // 2
        historical_occupancy_counts = [
            min(total_beds, max(2, base + int(i * 0.02) + random.randint(-1, 1)))
            for i in range(48)
        ]
        random.seed()

        current_occupied = historical_occupancy_counts[-1]
        los_low, los_high = ward.los_range

        return HospitalContext(
            hospital_id=hospital_id,
            unit_id=ward.unit_id,
            total_beds=total_beds,
            occupied_beds=current_occupied,
            admissions_24h=max(2, total_beds // 4),
            discharges_24h=max(1, total_beds // 6),
            staff_on_duty=ward.staff_day,
            average_los_hours=round((los_low + los_high) / 2, 1),
            timestamp=datetime.now(timezone.utc).isoformat(),
            historical_occupancy_counts=historical_occupancy_counts,
        )

    async def get_complete_snapshot(self, hospital_id: str, unit_id: str) -> CompleteHospitalSnapshot:
        """Retrieve the full 4-pillar snapshot for the requested ward."""
        ward = get_ward(unit_id)

        # Cycle through this ward's pre-generated 30-day data sequentially
        ward_records = self._ward_records(ward.unit_id)
        if ward_records:
            idx = self._data_indexes.get(ward.unit_id, 0)
            self._data_indexes[ward.unit_id] = idx + 1
            return CompleteHospitalSnapshot.model_validate(
                ward_records[idx % len(ward_records)]
            )

        # Otherwise generate a fresh snapshot from the ward profile
        ward = get_ward(unit_id)
        now = datetime.now(timezone.utc)
        hour = now.hour
        is_weekend = now.weekday() >= 5

        total_beds = ward.total_beds
        occ_floor = int(total_beds * ward.occupancy_floor_frac)
        occ_ceiling = int(total_beds * ward.occupancy_ceiling_frac)
        current_occupied = (occ_floor + occ_ceiling) // 2

        # ER Arrivals (diurnal pattern), scaled by this ward's ER pressure
        er_wave = math.sin((hour - 10) * math.pi / 12)
        er_waiting = max(
            1, int((5 + max(0, er_wave * 4)) * ward.er_pressure_scale) + random.randint(-2, 3)
        )
        er_boarders = max(0, int(er_waiting * 0.3) + random.randint(-1, 2))
        er_high_acuity = max(0, int(er_waiting * 0.2) + random.randint(0, 2))

        # Scheduled cases
        if ward.receives_electives and not is_weekend and 7 <= hour <= 11:
            elective = random.randint(2, 5)
            post_op_icu = random.randint(1, 2) if ward.unit_type == "ICU" else 0
        else:
            elective = 0
            post_op_icu = 0

        # Build 48h history
        random.seed(42)
        history = [
            min(total_beds, max(2, current_occupied + random.randint(-1, 1)))
            for _ in range(48)
        ]
        random.seed()

        los_low, los_high = ward.los_range
        census = WardCensusContext(
            unit_id=ward.unit_id,
            unit_type=ward.unit_type,
            total_beds=total_beds,
            occupied_beds=current_occupied,
            blocked_beds=2,
            admissions_24h=max(2, total_beds // 4),
            discharges_24h=max(1, total_beds // 6),
            pending_discharges_today=random.randint(1, 3),
            staff_on_duty=ward.staff_day if not is_weekend else ward.staff_night,
            average_los_hours=round((los_low + los_high) / 2, 1),
            bed_turnover_time_hours=2.0,
        )

        return CompleteHospitalSnapshot(
            timestamp=now.isoformat(),
            hospital_id=hospital_id,
            census=census,
            er_arrivals=ERArrivalsContext(
                er_current_waiting_count=er_waiting,
                er_admit_decisions_pending=er_boarders,
                er_high_acuity_arrivals_last_6h=er_high_acuity,
            ),
            scheduled_cases=ScheduledCasesContext(
                scheduled_elective_admissions_24h=elective,
                scheduled_post_op_icu_beds=post_op_icu,
                same_day_surgeries_count=random.randint(3, 8) if not is_weekend else 0,
            ),
            external_signals=[
                ExternalSignal(
                    signal_type="viral_epidemic_index",
                    value=0.82,
                    direction="increasing",
                    severity="high",
                    confidence=0.89,
                ),
                ExternalSignal(
                    signal_type="seasonality_weather",
                    value=0.35,
                    direction="stable",
                    severity="medium",
                    confidence=0.95,
                ),
            ],
            historical_occupancy_48h=history,
        )


class PublicSignalMCPClient:
    """Mock client simulating MCP integration for external epidemiological signals.

    In production, this would connect to CDC/WHO surveillance feeds,
    weather APIs, and local outbreak tracking systems.
    """

    async def get_signals(self, location: str) -> list[ExternalSignal]:
        """Retrieve public health and environmental signals for a location."""
        return [
            ExternalSignal(
                signal_type="viral_epidemic_index",
                value=0.82,
                direction="increasing",
                severity="high",
                confidence=0.89,
            ),
            ExternalSignal(
                signal_type="severe_weather_alert",
                value="extreme_cold",
                direction="stable",
                severity="medium",
                confidence=0.95,
            ),
        ]
