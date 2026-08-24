"""Generate 30 days (720 hours) of realistic hospital time-series mock data.

Hospital data follows strict real-world physics and biological patterns:
1. Physics: Occupied_Beds(t) = Occupied_Beds(t-1) + Admissions(t) - Discharges(t)
2. Diurnal Cycles (Time of Day):
   - ER Arrivals: Peak in late afternoon/evening (14:00 – 22:00)
   - Elective Surgeries: Admitted early morning (07:00 – 11:00)
   - Discharges: Occur late morning to afternoon (11:00 – 16:00)
3. Weekly Cycles (Day of Week):
   - Elective surgeries drop to 0 on weekends (Sat/Sun)
   - Discharges slow down significantly on weekends
4. Outbreak Surge: Simulated viral wave starting on Day 15 that pushes
   occupancy toward capacity limits.

Output: JSON file with 720 CompleteHospitalSnapshot records.

Usage:
    uv run python scripts/generate_mock_data.py
"""

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import (
    CompleteHospitalSnapshot,
    ERArrivalsContext,
    ExternalSignal,
    ScheduledCasesContext,
    WardCensusContext,
)

# Reproducible randomness
random.seed(42)


def generate_30_day_hospital_data(
    hospital_id: str = "HOSPITAL-MAIN-01",
    unit_id: str = "ICU-EAST",
    total_beds: int = 50,
) -> list[CompleteHospitalSnapshot]:
    """Generate 720 hours of realistic hospital snapshot data.

    Returns:
        List of CompleteHospitalSnapshot objects (one per hour for 30 days).
    """
    start_time = datetime.now(timezone.utc) - timedelta(days=30)
    snapshots: list[CompleteHospitalSnapshot] = []

    # Initial state
    current_occupied = 35
    history_48h = [32 + random.randint(-2, 2) for _ in range(48)]

    for hour_idx in range(720):
        current_timestamp = start_time + timedelta(hours=hour_idx)
        hour_of_day = current_timestamp.hour
        day_of_week = current_timestamp.weekday()  # 0=Monday, 6=Sunday
        is_weekend = day_of_week >= 5
        day_number = hour_idx // 24

        # ===================================================================
        # 1. External Signals — Outbreak wave starts around Day 15
        # ===================================================================
        outbreak_intensity = 0.2 if day_number < 15 else 0.2 + (day_number - 15) * 0.04
        outbreak_intensity = min(outbreak_intensity, 0.95)

        # Seasonal weather effect (sinusoidal cold snap)
        weather_severity = 0.3 + 0.2 * math.sin(day_number * math.pi / 15)

        external_signals = [
            ExternalSignal(
                signal_type="flu_outbreak_index",
                value=round(outbreak_intensity, 3),
                direction="increasing" if day_number >= 15 else "stable",
                severity=(
                    "critical" if outbreak_intensity > 0.8
                    else "high" if outbreak_intensity > 0.6
                    else "medium" if outbreak_intensity > 0.4
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

        # ===================================================================
        # 2. ER Arrivals — Peaks between 14:00 - 22:00 (sinusoidal)
        # ===================================================================
        er_wave = math.sin((hour_of_day - 10) * math.pi / 12)  # Peak ~16:00
        er_base = 5 + int(max(0, er_wave * 4)) + int(outbreak_intensity * 5)
        er_waiting = max(1, er_base + random.randint(-2, 3))
        er_boarders = max(0, int(er_waiting * 0.3) + random.randint(-1, 2))
        er_high_acuity = max(0, int(er_waiting * 0.2) + random.randint(0, 2))

        er_arrivals = ERArrivalsContext(
            er_current_waiting_count=er_waiting,
            er_admit_decisions_pending=er_boarders,
            er_high_acuity_arrivals_last_6h=er_high_acuity,
        )

        # ===================================================================
        # 3. Scheduled Cases — Elective surgeries weekday mornings only
        # ===================================================================
        if not is_weekend and 7 <= hour_of_day <= 11:
            elective_admissions = random.randint(2, 5)
            post_op_icu = random.randint(1, 2)
            same_day = random.randint(3, 8)
        else:
            elective_admissions = 0
            post_op_icu = 0
            same_day = random.randint(1, 3) if not is_weekend else 0

        scheduled_cases = ScheduledCasesContext(
            scheduled_elective_admissions_24h=elective_admissions * 3 if 7 <= hour_of_day <= 8 else elective_admissions,
            scheduled_post_op_icu_beds=post_op_icu,
            same_day_surgeries_count=same_day,
        )

        # ===================================================================
        # 4. Discharges — Peak 11:00-16:00, reduced on weekends
        # ===================================================================
        if 11 <= hour_of_day <= 16:
            discharge_rate = random.randint(1, 4) if not is_weekend else random.randint(0, 2)
        else:
            discharge_rate = random.randint(0, 1)

        # ===================================================================
        # 5. Physics Engine: Update Occupied Beds
        #    Beds(t) = Beds(t-1) + Admissions(t) - Discharges(t)
        # ===================================================================
        # ER admissions: boarders that get beds
        er_admissions = 1 if (random.random() < (er_boarders * 0.2)) else 0
        new_admissions = elective_admissions + er_admissions
        actual_discharges = min(current_occupied, discharge_rate)

        current_occupied = current_occupied + new_admissions - actual_discharges
        current_occupied = max(5, min(total_beds, current_occupied))

        # Blocked beds (infection control / cleaning)
        blocked_beds = 2 if current_occupied > 40 else 1

        # Pending discharges (physicians have written discharge orders)
        pending_discharges = max(1, random.randint(2, 6)) if 6 <= hour_of_day <= 20 else random.randint(0, 2)

        # Rolling admissions/discharges sum for the 24h window
        admissions_24h = random.randint(8, 14) + int(outbreak_intensity * 4)
        discharges_24h = random.randint(5, 10)

        # Maintain 48-hour sliding window for TimesFM
        history_48h.pop(0)
        history_48h.append(current_occupied)

        # Staff (reduced on weekends and nights)
        if is_weekend:
            staff = 6 if 7 <= hour_of_day <= 19 else 4
        else:
            staff = 8 if 7 <= hour_of_day <= 19 else 5

        # ===================================================================
        # Build CompleteHospitalSnapshot
        # ===================================================================
        census = WardCensusContext(
            unit_id=unit_id,
            unit_type="ICU",
            total_beds=total_beds,
            occupied_beds=current_occupied,
            blocked_beds=blocked_beds,
            admissions_24h=admissions_24h,
            discharges_24h=discharges_24h,
            pending_discharges_today=pending_discharges,
            staff_on_duty=staff,
            average_los_hours=44.5,
            bed_turnover_time_hours=2.5 if current_occupied > 45 else 1.8,
        )

        snapshot = CompleteHospitalSnapshot(
            timestamp=current_timestamp.isoformat(),
            hospital_id=hospital_id,
            census=census,
            er_arrivals=er_arrivals,
            scheduled_cases=scheduled_cases,
            external_signals=external_signals,
            historical_occupancy_48h=list(history_48h),
        )

        snapshots.append(snapshot)

    return snapshots


def main() -> None:
    """Generate and save 30-day hospital mock data to JSON."""
    print("[*] Generating 30 days (720 hours) of hospital time-series mock data...")
    print("    Hospital: HOSPITAL-MAIN-01 | Unit: ICU-EAST | Beds: 50")
    print("    Features: Diurnal cycles, weekly patterns, outbreak surge on Day 15")
    print()

    dataset = generate_30_day_hospital_data()

    # Save as JSON
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "hospital_30day_mock_data.json"

    with open(output_file, "w") as f:
        json.dump([s.model_dump() for s in dataset], f, indent=2)

    # Print summary statistics
    occupancy_series = [s.census.occupied_beds for s in dataset]
    er_boarders_series = [s.er_arrivals.er_admit_decisions_pending for s in dataset]

    print(f"[OK] Generated {len(dataset)} hourly records")
    print(f"    Output: {output_file}")
    print()
    print("    --- Summary Statistics ---")
    print(f"    Occupancy Range  : {min(occupancy_series)} - {max(occupancy_series)} beds")
    print(f"    Mean Occupancy   : {sum(occupancy_series) / len(occupancy_series):.1f} beds")
    print(f"    Max ER Boarders  : {max(er_boarders_series)}")
    print(f"    Outbreak Peak    : Day 15+ (intensity up to 0.95)")
    print()

    # Print first and last snapshot samples
    print("    --- First Snapshot (Hour 0) ---")
    first = dataset[0]
    print(f"    Timestamp: {first.timestamp}")
    print(f"    Occupied: {first.census.occupied_beds}/{first.census.total_beds}")
    print(f"    ER Waiting: {first.er_arrivals.er_current_waiting_count}")
    print(f"    Outbreak: {first.external_signals[0].value}")
    print()

    print("    --- Last Snapshot (Hour 719) ---")
    last = dataset[-1]
    print(f"    Timestamp: {last.timestamp}")
    print(f"    Occupied: {last.census.occupied_beds}/{last.census.total_beds}")
    print(f"    ER Waiting: {last.er_arrivals.er_current_waiting_count}")
    print(f"    Outbreak: {last.external_signals[0].value}")


if __name__ == "__main__":
    main()
