"""Generate 30 days (720 hours) of realistic per-ward hospital mock data.

Hospital data follows strict real-world physics and biological patterns:
1. Physics: Occupied_Beds(t) = Occupied_Beds(t-1) + Admissions(t) - Discharges(t)
2. Diurnal Cycles (Time of Day):
   - ER Arrivals: Peak in late afternoon/evening (14:00 – 22:00)
   - Elective Surgeries: Admitted early morning (07:00 – 11:00)
   - Discharges: Occur late morning to afternoon (11:00 – 16:00)
3. Weekly Cycles (Day of Week):
   - Elective surgeries drop to 0 on weekends (Sat/Sun)
   - Discharges slow down significantly on weekends
4. Outbreak Surge: Simulated viral wave starting on Day 15.
5. Multi-Ward: Every ward in app/data/wards.py is generated with its own
   capacity profile, admission-source mix, and LOS range.

Output: JSON file with 720 records PER WARD (wards concatenated).

Usage:
    uv run python scripts/generate_mock_data.py [--single ICU-EAST]
"""

import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.data.wards import WARDS, WardProfile, get_ward
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
    ward: WardProfile,
    hospital_id: str = "HOSPITAL-MAIN-01",
) -> list[CompleteHospitalSnapshot]:
    """Generate 720 hours of realistic snapshot data for one ward."""
    total_beds = ward.total_beds
    start_time = datetime.now(timezone.utc) - timedelta(days=30)
    snapshots: list[CompleteHospitalSnapshot] = []

    # Occupancy band derived from the ward's profile
    occ_floor = max(3, int(total_beds * ward.occupancy_floor_frac))
    occ_ceiling = min(total_beds - 1, int(total_beds * ward.occupancy_ceiling_frac))
    current_occupied = int((occ_floor + occ_ceiling) / 2)
    history_48h = [
        max(occ_floor, min(occ_ceiling, current_occupied + random.randint(-2, 2)))
        for _ in range(48)
    ]

    # Per-hour admission/discharge rates scale with ward size
    rate_scale = total_beds / 50.0

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
        # 2. ER Arrivals — Peaks between 14:00 - 22:00 (sinusoidal),
        #    scaled by this ward's share of ER pressure
        # ===================================================================
        er_wave = math.sin((hour_of_day - 10) * math.pi / 12)  # Peak ~16:00
        er_base = (
            5 * ward.er_pressure_scale
            + int(max(0, er_wave * 4)) * ward.er_pressure_scale
            + int(outbreak_intensity * 5) * ward.er_pressure_scale
        )
        er_base = max(1, int(round(er_base)))
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
        if ward.receives_electives and not is_weekend and 7 <= hour_of_day <= 11:
            elective_admissions = max(
                1, int(round(random.randint(2, 5) * rate_scale * ward.elective_weight / 0.2))
            )
            post_op_icu = random.randint(1, 2) if ward.unit_type == "ICU" else 0
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
            discharge_rate = random.randint(1, 2) if not is_weekend else (1 if random.random() < 0.5 else 0)
        else:
            discharge_rate = 1 if random.random() < 0.25 else 0

        # ===================================================================
        # 5. Physics Engine — source-aware admission mix
        # ===================================================================
        roll = random.random()
        if roll < ward.er_admit_weight:
            new_admissions = 1 if random.random() < min(0.6, er_boarders * 0.12) else 0
        elif roll < ward.er_admit_weight + ward.transfer_in_weight:
            # Inter-ward transfer (e.g. ICU step-down): weekday daytime biased
            new_admissions = (
                1 if (not is_weekend and 9 <= hour_of_day <= 18 and random.random() < 0.18)
                else 0
            )
        elif ward.receives_electives:
            new_admissions = 1 if (elective_admissions and random.random() < 0.25) else 0
        else:
            new_admissions = 1 if random.random() < 0.04 else 0

        actual_discharges = min(current_occupied - occ_floor + 1, discharge_rate) if current_occupied > occ_floor - 1 else 0

        current_occupied = current_occupied + new_admissions - actual_discharges

        # Soft pull back into the ward's occupancy band when overflowing
        if current_occupied > occ_ceiling:
            current_occupied = occ_ceiling if random.random() < 0.7 else current_occupied - 1
        current_occupied = max(max(2, occ_floor - 2), min(total_beds, current_occupied))

        # Blocked beds (infection control / cleaning)
        blocked_beds = 2 if current_occupied > int(total_beds * 0.85) else 1

        # Pending discharges (physicians have written discharge orders)
        pending_discharges = (
            max(1, int(random.randint(2, 6) * rate_scale)) if 6 <= hour_of_day <= 20
            else random.randint(0, 2)
        )

        # Rolling admissions/discharges sum for the 24h window
        admissions_24h = int(random.randint(8, 14) * rate_scale) + int(outbreak_intensity * 4)
        discharges_24h = int(random.randint(5, 10) * rate_scale)

        # Maintain 48-hour sliding window for TimesFM
        history_48h.pop(0)
        history_48h.append(current_occupied)

        # Staff (reduced on weekends and nights)
        if is_weekend:
            staff = ward.staff_day - 2 if 7 <= hour_of_day <= 19 else ward.staff_night - 1
        else:
            staff = ward.staff_day if 7 <= hour_of_day <= 19 else ward.staff_night

        # ===================================================================
        # Build CompleteHospitalSnapshot
        # ===================================================================
        los_low, los_high = ward.los_range
        census = WardCensusContext(
            unit_id=ward.unit_id,
            unit_type=ward.unit_type,
            total_beds=total_beds,
            occupied_beds=current_occupied,
            blocked_beds=blocked_beds,
            admissions_24h=admissions_24h,
            discharges_24h=discharges_24h,
            pending_discharges_today=pending_discharges,
            staff_on_duty=staff,
            average_los_hours=round(random.uniform(los_low, los_high), 1),
            bed_turnover_time_hours=2.5 if current_occupied > int(total_beds * 0.9) else 1.8,
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
    """Generate and save 30-day multi-ward mock data to JSON."""
    single_unit = sys.argv[sys.argv.index("--single") + 1] if "--single" in sys.argv else None
    wards = [get_ward(single_unit)] if single_unit else WARDS

    print(f"[*] Generating 30 days (720 hours) x {len(wards)} wards of mock data...")
    print(f"    Wards: {', '.join(w.unit_id for w in wards)}")
    print()

    dataset: list[CompleteHospitalSnapshot] = []
    for ward in wards:
        ward_data = generate_30_day_hospital_data(ward)
        dataset.extend(ward_data)
        occ = [s.census.occupied_beds for s in ward_data]
        print(
            f"    [✓] {ward.unit_id:<14} ({ward.unit_type:<9}) "
            f"{len(ward_data)} recs | beds {ward.total_beds} | "
            f"occupancy {min(occ)}-{max(occ)} (mean {sum(occ)/len(occ):.1f})"
        )

    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "hospital_30day_mock_data.json"

    with open(output_file, "w") as f:
        json.dump([s.model_dump() for s in dataset], f)

    print()
    print(f"[OK] Generated {len(dataset)} hourly records across {len(wards)} ward(s)")
    print(f"     Output: {output_file}")


if __name__ == "__main__":
    main()
