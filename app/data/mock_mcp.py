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


class HospitalMCPClient:
    """Mock client simulating Model Context Protocol (MCP) tool integration with EMR/EHR.

    Provides:
    - get_hospital_context(): Basic context (backward compat for agent pipeline)
    - get_complete_snapshot(): Full 4-pillar snapshot for advanced forecasting
    """

    def __init__(self) -> None:
        # Try to load pre-generated 30-day data for richer context
        self._mock_data: list[dict] | None = None
        self._data_index = 0
        data_file = Path(__file__).parent.parent.parent / "data" / "hospital_30day_mock_data.json"
        if data_file.exists():
            try:
                self._mock_data = json.loads(data_file.read_text())
            except Exception:
                pass

    async def get_hospital_context(self, hospital_id: str, unit_id: str) -> HospitalContext:
        """Retrieve current bed, staffing state, and 48-hour occupancy history.

        Returns the basic HospitalContext used by the existing agent pipeline.
        """
        total_beds = 50

        # Generate 48 hours of past occupancy counts with realistic upward trend
        random.seed(42)
        base_occupancy = 38
        historical_occupancy_counts = [
            min(total_beds, max(20, base_occupancy + int(i * 0.1) + random.randint(-1, 1)))
            for i in range(48)
        ]
        random.seed()

        current_occupied = historical_occupancy_counts[-1]

        return HospitalContext(
            hospital_id=hospital_id,
            unit_id=unit_id,
            total_beds=total_beds,
            occupied_beds=current_occupied,
            admissions_24h=12,
            discharges_24h=5,
            staff_on_duty=6,
            average_los_hours=42.0,
            timestamp=datetime.now(timezone.utc).isoformat(),
            historical_occupancy_counts=historical_occupancy_counts,
        )

    async def get_complete_snapshot(self, hospital_id: str, unit_id: str) -> CompleteHospitalSnapshot:
        """Retrieve the full 4-pillar hospital snapshot.

        If 30-day mock data is available, cycles through it sequentially.
        Otherwise generates a realistic point-in-time snapshot.
        """
        # If pre-generated data available, use it
        if self._mock_data:
            record = self._mock_data[self._data_index % len(self._mock_data)]
            self._data_index += 1
            return CompleteHospitalSnapshot.model_validate(record)

        # Otherwise generate a fresh snapshot
        now = datetime.now(timezone.utc)
        hour = now.hour
        is_weekend = now.weekday() >= 5

        total_beds = 50
        current_occupied = 43

        # ER Arrivals (diurnal pattern)
        er_wave = math.sin((hour - 10) * math.pi / 12)
        er_waiting = max(1, 5 + int(max(0, er_wave * 4)) + random.randint(-2, 3))
        er_boarders = max(0, int(er_waiting * 0.3) + random.randint(-1, 2))
        er_high_acuity = max(0, int(er_waiting * 0.2) + random.randint(0, 2))

        # Scheduled cases
        if not is_weekend and 7 <= hour <= 11:
            elective = random.randint(2, 5)
            post_op_icu = random.randint(1, 2)
        else:
            elective = 0
            post_op_icu = 0

        # Build 48h history
        history = [
            min(total_beds, max(20, 38 + int(i * 0.1) + random.randint(-1, 1)))
            for i in range(48)
        ]

        census = WardCensusContext(
            unit_id=unit_id,
            unit_type="ICU",
            total_beds=total_beds,
            occupied_beds=current_occupied,
            blocked_beds=2,
            admissions_24h=12,
            discharges_24h=5,
            pending_discharges_today=4,
            staff_on_duty=8 if not is_weekend else 6,
            average_los_hours=44.5,
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
