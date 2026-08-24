"""What-if scenario simulation engine for bed occupancy strategy planning.

Takes the live TimesFM feature pipeline and re-runs forecasting under
modified assumptions:

- bed_delta:          add/remove staffed beds (changes capacity, not rates)
- elective_deferral_pct: shift a % of scheduled elective admissions out of
                        the forecast window (zeroes elective inflow covariate)
- er_surge_pct:       amplify/dampen recent ER boarder pressure conditioning

All three re-use the SAME trained foundation model — no retraining, ~0.15s
per inference on CPU.
"""

from typing import Any

import numpy as np


def apply_scenario_to_features(
    features: dict[str, Any],
    bed_delta: int = 0,
    elective_deferral_pct: float = 0.0,
    er_surge_pct: float = 0.0,
) -> dict[str, Any]:
    """Returns a copy of the feature dict with scenario knobs applied."""
    scenario = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in features.items()}

    # ── Bed capacity delta: adjust static capacity only (rates unchanged) ──
    if bed_delta != 0:
        static = scenario["static_covariates"]
        static[0] = max(1.0, float(static[0]) + bed_delta)

    # ── Elective deferral: proportionally reduce elective inflow ──
    if elective_deferral_pct > 0:
        future = scenario["future_covariates"]
        future[:, 0] = future[:, 0] * (1.0 - elective_deferral_pct / 100.0)

    # ── ER surge: amplify recent boarder pressure in past covariates ──
    if er_surge_pct != 0:
        factor = 1.0 + (er_surge_pct / 100.0)
        past = scenario["past_covariates"]
        past[-6:, 0] = np.clip(past[-6:, 0] * factor, 0.0, 1.0)  # col 0 = boarder pressure
        past[-6:, 1] = np.clip(past[-6:, 1] * factor, 0.0, 1.0)  # col 1 = waiting pressure

    return scenario


def summarize_scenario(
    baseline_points: list[dict],
    scenario_points: list[dict],
) -> dict[str, Any]:
    """Compares baseline vs scenario hourly point lists.

    Both are ForecastPoint dumps with predicted_occupancy / lower_bound /
    upper_bound. Returns summary deltas for UI chips.
    """
    base_occ = [p["predicted_occupancy"] for p in baseline_points]
    scen_occ = [p["predicted_occupancy"] for p in scenario_points]

    n = min(len(base_occ), len(scen_occ))
    if n == 0:
        return {"peak_delta": 0.0, "mean_delta": 0.0, "beds_freed_avg": 0.0}

    peak_base = max(base_occ[:n])
    peak_scen = max(scen_occ[:n])
    mean_base = float(np.mean(base_occ[:n]))
    mean_scen = float(np.mean(scen_occ[:n]))

    return {
        "peak_delta": round((peak_scen - peak_base) * 100, 2),   # % points
        "mean_delta": round((mean_scen - mean_base) * 100, 2),   # % points
        "mean_occupancy": round(mean_scen, 4),
        "peak_occupancy": round(peak_scen, 4),
    }
