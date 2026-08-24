"""Scenario-driven hospital mock data engine.

5 data regimes: balanced / high_capacity / volatile / recovery /
outbreak_surge. Used by the CLI script (scripts/generate_mock_data_10_beds.py)
and the live POST /api/mock/regenerate endpoint.
"""

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.models import (
    CompleteHospitalSnapshot,
    ERArrivalsContext,
    ExternalSignal,
    ScheduledCasesContext,
    WardCensusContext,
)


# ============================================================================
# SCENARIO CONFIGURATIONS
# ============================================================================
@dataclass
class ScenarioConfig:
    """Shapes every driver of the physics engine."""

    name: str
    description: str

    # Outbreak intensity as a function of day number
    outbreak_curve: Callable[[int], float]
    # Baseline occupancy band the physics engine is pulled toward
    occupancy_floor: int
    occupancy_ceiling: int
    # Multipliers on the base rates
    er_multiplier: float = 1.0
    elective_multiplier: float = 1.0
    discharge_efficiency: float = 1.0  # 1.0 normal; <1 slows discharges
    # Length-of-stay range (hours)
    los_range: tuple[float, float] = (38.0, 46.0)
    bed_turnover_hours: float = 1.2
    # Agency staffing kicks in at this occupancy
    staffing_surge_at: int = 10
    # Recurring mini-waves for volatile regime
    wave_period_days: int = 0


SCENARIOS: dict[str, ScenarioConfig] = {
    "balanced": ScenarioConfig(
        name="balanced",
        description="Nominal operations — 50-70% occupancy, no surge",
        outbreak_curve=lambda day: 0.15,
        occupancy_floor=5,
        occupancy_ceiling=7,
        er_multiplier=0.8,
        elective_multiplier=1.0,
        discharge_efficiency=1.0,
        los_range=(36.0, 44.0),
        staffing_surge_at=11,
    ),
    "high_capacity": ScenarioConfig(
        name="high_capacity",
        description="Chronic crisis — 85-100% saturation, boarder backlog",
        outbreak_curve=lambda day: min(0.9, 0.55 + day * 0.01),
        occupancy_floor=8,
        occupancy_ceiling=10,
        er_multiplier=1.8,
        elective_multiplier=1.2,
        discharge_efficiency=0.6,
        los_range=(48.0, 58.0),
        bed_turnover_hours=1.8,
        staffing_surge_at=9,
    ),
    "volatile": ScenarioConfig(
        name="volatile",
        description="Recurring 2-3 day mini-waves — anomaly-detection fodder",
        outbreak_curve=lambda day: 0.3 + 0.45 * max(
            0.0, math.sin(day * 2 * math.pi / 4)
        ),
        occupancy_floor=4,
        occupancy_ceiling=10,
        er_multiplier=1.4,
        elective_multiplier=1.0,
        discharge_efficiency=0.85,
        los_range=(38.0, 50.0),
        staffing_surge_at=9,
        wave_period_days=4,
    ),
    "recovery": ScenarioConfig(
        name="recovery",
        description="Post-crisis ramp-down — 95% saturating to 55%",
        outbreak_curve=lambda day: max(0.1, 0.85 - day * 0.03),
        occupancy_floor=5,
        occupancy_ceiling=10,
        er_multiplier=0.9,
        elective_multiplier=0.8,
        discharge_efficiency=1.6,  # discharge blitz
        los_range=(34.0, 40.0),
        bed_turnover_hours=1.0,
        staffing_surge_at=10,
    ),
    "outbreak_surge": ScenarioConfig(
        name="outbreak_surge",
        description="Legacy narrative — outbreak wave starting Day 15",
        outbreak_curve=lambda day: min(0.95, 0.2) if day < 15 else min(
            0.95, 0.2 + (day - 15) * 0.04
        ),
        occupancy_floor=5,
        occupancy_ceiling=10,
        er_multiplier=1.0,
        elective_multiplier=1.0,
        discharge_efficiency=1.0,
        los_range=(38.0, 46.0),
        staffing_surge_at=10,
    ),
}


def generate_scenario_data(
    scenario: str = "outbreak_surge",
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
    total_beds: int = 10,
    days: int = 30,
    seed: int | None = None,
) -> list[CompleteHospitalSnapshot]:
    """Generate `days*24` hourly snapshots under the requested regime."""
    cfg = SCENARIOS.get(scenario)
    if cfg is None:
        raise ValueError(f"Unknown scenario '{scenario}'. Options: {list(SCENARIOS)}")

    rng = random.Random(seed if seed is not None else random.randint(0, 2**31 - 1))

    start_time = datetime.now(timezone.utc) - timedelta(days=days)
    snapshots: list[CompleteHospitalSnapshot] = []

    band_mid = (cfg.occupancy_floor + cfg.occupancy_ceiling) / 2
    current_occupied = int(band_mid)
    history_48h = [
        max(cfg.occupancy_floor, min(cfg.occupancy_ceiling, current_occupied + rng.randint(-1, 1)))
        for _ in range(48)
    ]

    hours = days * 24
    for hour_idx in range(hours):
        ts = start_time + timedelta(hours=hour_idx)
        hour_of_day = ts.hour
        day_of_week = ts.weekday()
        is_weekend = day_of_week >= 5
        day_number = hour_idx // 24

        # ── 1. External signals from the scenario curve ──
        outbreak = min(0.95, cfg.outbreak_curve(day_number))
        weather_severity = 0.3 + 0.2 * math.sin(day_number * math.pi / 15)

        external_signals = [
            ExternalSignal(
                signal_type="flu_outbreak_index",
                value=round(outbreak, 3),
                direction="increasing" if outbreak > 0.5 else "stable",
                severity=(
                    "critical" if outbreak > 0.8
                    else "high" if outbreak > 0.6
                    else "medium" if outbreak > 0.4
                    else "low"
                ),
                confidence=0.91,
            ),
            ExternalSignal(
                signal_type="seasonality_weather",
                value=round(weather_severity, 3),
                direction="increasing" if weather_severity > 0.4 else "stable",
                severity="medium" if weather_severity > 0.35 else "low",
                confidence=0.95,
            ),
        ]

        # ── 2. ER arrivals (diurnal peak ~16:00, scaled by regime) ──
        er_wave = math.sin((hour_of_day - 10) * math.pi / 12)
        er_base = 1 + int(max(0, er_wave * 2)) + int(outbreak * 2)
        er_base = max(1, round(er_base * cfg.er_multiplier))
        er_waiting = max(1, er_base + rng.randint(-1, 1))
        er_boarders = max(0, int(er_waiting * 0.4) + (1 if outbreak > 0.6 else 0))
        er_high_acuity = max(0, int(er_waiting * 0.3))

        er_arrivals = ERArrivalsContext(
            er_current_waiting_count=er_waiting,
            er_admit_decisions_pending=er_boarders,
            er_high_acuity_arrivals_last_6h=er_high_acuity,
        )

        # ── 3. Elective surgeries (weekday mornings, regime-scaled) ──
        elective_active = not is_weekend and 7 <= hour_of_day <= 11
        if elective_active:
            elective_admissions = max(
                1, round(rng.randint(1, 2) * cfg.elective_multiplier)
            )
            post_op_icu = rng.randint(0, 1)
            same_day = rng.randint(1, 2)
        else:
            elective_admissions = 0
            post_op_icu = 0
            same_day = rng.randint(0, 1) if not is_weekend else 0

        scheduled_cases = ScheduledCasesContext(
            scheduled_elective_admissions_24h=(
                elective_admissions * 2 if 7 <= hour_of_day <= 8 else elective_admissions
            ),
            scheduled_post_op_icu_beds=post_op_icu,
            same_day_surgeries_count=same_day,
        )

        # ── 4. Discharges (peak 11:00-16:00, regime efficiency) ──
        if 11 <= hour_of_day <= 16:
            discharge_rate = rng.randint(1, 2) if not is_weekend else rng.randint(0, 1)
        else:
            discharge_rate = 1 if rng.random() < 0.2 else 0
        discharge_rate = round(discharge_rate * cfg.discharge_efficiency)

        # ── 5. Physics: beds(t) = beds(t-1) + admissions - discharges ──
        er_admissions = 1 if rng.random() < (er_boarders * 0.3) else 0
        new_admissions = elective_admissions + er_admissions

        headroom_free = current_occupied > cfg.occupancy_floor
        actual_discharges = (
            min(current_occupied - cfg.occupancy_floor, discharge_rate) if headroom_free else 0
        )

        current_occupied += new_admissions - actual_discharges

        # Soft pull toward the regime's ceiling when admissions overflow it
        if current_occupied > cfg.occupancy_ceiling:
            current_occupied = cfg.occupancy_ceiling if rng.random() < 0.75 else current_occupied - 1
        current_occupied = max(cfg.occupancy_floor, min(total_beds, current_occupied))

        blocked_beds = 1 if current_occupied >= 8 else 0
        pending_discharges = (
            max(1, rng.randint(1, 3)) if 8 <= hour_of_day <= 17 else rng.randint(0, 1)
        )

        admissions_24h = rng.randint(2, 5) + int(outbreak * 2)
        discharges_24h = rng.randint(2, 4)

        history_48h.pop(0)
        history_48h.append(current_occupied)

        # ── 6. Staffing reacts to load ──
        if 7 <= hour_of_day <= 19:
            staff_count = 3
        else:
            staff_count = 2
        if current_occupied >= cfg.staffing_surge_at:
            staff_count += 1  # agency nurse

        los_low, los_high = cfg.los_range
        census = WardCensusContext(
            unit_id=unit_id,
            unit_type="ICU",
            total_beds=total_beds,
            occupied_beds=current_occupied,
            blocked_beds=blocked_beds,
            admissions_24h=admissions_24h,
            discharges_24h=discharges_24h,
            pending_discharges_today=pending_discharges,
            staff_on_duty=staff_count,
            average_los_hours=round(rng.uniform(los_low, los_high), 1),
            bed_turnover_time_hours=(
                cfg.bed_turnover_hours * (1.25 if current_occupied > 8 else 1.0)
            ),
        )

        snapshots.append(
            CompleteHospitalSnapshot(
                timestamp=ts.isoformat(),
                hospital_id=hospital_id,
                census=census,
                er_arrivals=er_arrivals,
                scheduled_cases=scheduled_cases,
                external_signals=external_signals,
                historical_occupancy_48h=list(history_48h),
            )
        )

    return snapshots




def generate_patient_stays(
    snapshots: list[CompleteHospitalSnapshot],
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "FLOOR-1",
) -> list[dict]:
    """Derives patient-level stay records from the generated snapshot timeline.

    Each admission (occupancy increase) opens a stay with plausible acuity
    attributes; the matching occupancy decrease (or dataset end) closes it.
    Output feeds the LOS prediction model's training data.
    """
    rng = random.Random(1234)
    stays: list[dict] = []
    open_stays: list[dict] = []
    mrn_counter = 100000

    def _new_stay(ts: datetime, outbreak: float) -> dict:
        nonlocal mrn_counter
        mrn_counter += 1
        esi = rng.choices([1, 2, 3, 4, 5], weights=[5, 25, 45, 20, 5])[0]
        # Sicker patients (low ESI) during outbreaks stay longer
        los_base = 40 - esi * 3 + outbreak * 14 + rng.uniform(-8, 12)
        return {
            "mrn": f"MRN{mrn_counter}",
            "esi_level": esi,
            "required_bed_type": rng.choices(
                ["ICU", "MED_SURG", "TELEMETRY", "STEP_DOWN", "ISOLATION"],
                weights=[15, 40, 20, 15, 10],
            )[0],
            "isolation_required": rng.random() < 0.08,
            "admitted_at": ts.isoformat(),
            "admit_hour": ts.hour,
            "admit_dow": ts.weekday(),
            "outbreak_intensity": round(outbreak, 2),
            "expected_los_hours": round(max(6.0, los_base), 1),
        }

    for i in range(1, len(snapshots)):
        s = snapshots[i]
        ts = datetime.fromisoformat(s.timestamp.replace("Z", "+00:00"))
        outbreak = next(
            (
                float(sig.value)
                for sig in s.external_signals
                if sig.signal_type == "flu_outbreak_index"
            ),
            0.2,
        )
        delta = s.census.occupied_beds - snapshots[i - 1].census.occupied_beds

        while delta > 0:  # admission(s)
            open_stays.append(_new_stay(ts, outbreak))
            delta -= 1
        while delta < 0 and open_stays:  # discharge(s) — close longest-staying first
            oldest = max(range(len(open_stays)), key=lambda i: open_stays[i]["admitted_at"])
            stay = open_stays.pop(oldest)
            los = (ts - datetime.fromisoformat(stay["admitted_at"])).total_seconds() / 3600
            stay["actual_los_hours"] = round(max(4.0, los), 1)
            stays.append(stay)
            delta += 1

    # Close still-open stays with their expected estimate (right-censored)
    for stay in open_stays:
        stay["actual_los_hours"] = stay["expected_los_hours"]
        stays.append(stay)

    return stays
