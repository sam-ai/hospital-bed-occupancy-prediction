"""Ward registry: multi-ward hospital configuration.

Defines every ward/unit the platform models. Each ward carries its own
capacity profile, staffing, length-of-stay range, and admission-source
mix so mock data, forecasting, and dashboards are all ward-aware.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class WardProfile:
    """Static configuration for a single hospital unit/ward."""

    unit_id: str
    display_name: str
    unit_type: str  # ICU | MED_SURG | STEP_DOWN | ER
    total_beds: int

    # Staffing (day shift 07-19 / night shift otherwise)
    staff_day: int = 8
    staff_night: int = 5

    # Length-of-stay range (hours)
    los_range: tuple[float, float] = (38.0, 50.0)

    # Occupancy band (fraction of total_beds the physics engine pulls toward)
    occupancy_floor_frac: float = 0.55
    occupancy_ceiling_frac: float = 0.92

    # Admission-source mix (weights sum to 1.0)
    er_admit_weight: float = 0.5       # direct admits via ER boarders
    elective_weight: float = 0.2       # scheduled OR admissions
    transfer_in_weight: float = 0.3    # transfers from other wards (e.g. ICU -> general)

    # Fraction of this ward's discharges that transfer onward (ICU -> wards)
    transfer_out_rate: float = 0.0

    # Relative scale on hospital-wide ER arrival pressure seen by this ward
    er_pressure_scale: float = 1.0

    # Elective surgery intake (post-op patients land here)
    receives_electives: bool = True


WARDS: list[WardProfile] = [
    WardProfile(
        unit_id="ICU-EAST",
        display_name="ICU East",
        unit_type="ICU",
        total_beds=10,
        staff_day=4,
        staff_night=3,
        los_range=(60.0, 96.0),
        occupancy_floor_frac=0.62,
        occupancy_ceiling_frac=0.92,
        er_admit_weight=0.65,
        elective_weight=0.15,
        transfer_in_weight=0.20,   # post-op from OR recovery
        transfer_out_rate=0.55,    # most ICU discharges step down to wards
        er_pressure_scale=1.6,
        receives_electives=True,
    ),
    WardProfile(
        unit_id="GENERAL-MALE",
        display_name="General Male",
        unit_type="MED_SURG",
        total_beds=10,
        staff_day=3,
        staff_night=2,
        los_range=(48.0, 84.0),
        occupancy_floor_frac=0.58,
        occupancy_ceiling_frac=0.90,
        er_admit_weight=0.35,
        elective_weight=0.25,
        transfer_in_weight=0.40,   # heavy ICU step-down inflow
        er_pressure_scale=1.1,
        receives_electives=True,
    ),
    WardProfile(
        unit_id="GENERAL-FEMALE",
        display_name="General Female",
        unit_type="MED_SURG",
        total_beds=10,
        staff_day=3,
        staff_night=2,
        los_range=(44.0, 78.0),
        occupancy_floor_frac=0.55,
        occupancy_ceiling_frac=0.88,
        er_admit_weight=0.40,
        elective_weight=0.30,
        transfer_in_weight=0.30,
        er_pressure_scale=1.0,
        receives_electives=True,
    ),
    WardProfile(
        unit_id="STEP-DOWN",
        display_name="Step-Down Unit",
        unit_type="STEP_DOWN",
        total_beds=10,
        staff_day=3,
        staff_night=2,
        los_range=(24.0, 48.0),
        occupancy_floor_frac=0.50,
        occupancy_ceiling_frac=0.85,
        er_admit_weight=0.20,
        elective_weight=0.10,
        transfer_in_weight=0.70,   # predominantly ICU step-down transfers
        er_pressure_scale=0.5,
        receives_electives=False,
    ),
]

WARDS_BY_ID: dict[str, WardProfile] = {w.unit_id: w for w in WARDS}
DEFAULT_UNIT_ID = WARDS[0].unit_id


def get_ward(unit_id: str) -> WardProfile:
    """Resolve a ward profile; falls back to the default ward if unknown."""
    return WARDS_BY_ID.get(unit_id, WARDS[0])
