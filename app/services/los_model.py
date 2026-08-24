"""Length-of-Stay prediction model (guide §3.3 / §8 Random Forest row).

A small RandomForestRegressor trained lazily on patient-stay records
derived from the mock snapshot timeline (app/data/mock_regimes.py).

Features: esi_level, required_bed_type, isolation_required,
admit_hour, admit_dow, outbreak_intensity.
Target: actual_los_hours.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

STAYS_FILE = Path(__file__).parent.parent.parent / "data" / "patient_stays.json"
BED_TYPE_INDEX = {"ICU": 0, "MED_SURG": 1, "TELEMETRY": 2, "STEP_DOWN": 3, "ISOLATION": 4}


def _featurize(stay: dict) -> list[float]:
    return [
        float(stay["esi_level"]),
        float(BED_TYPE_INDEX.get(stay.get("required_bed_type", "MED_SURG"), 1)),
        1.0 if stay.get("isolation_required") else 0.0,
        float(stay.get("admit_hour", 12)),
        float(stay.get("admit_dow", 0)),
        float(stay.get("outbreak_intensity", 0.2)),
    ]


@lru_cache(maxsize=1)
def _load_training_data() -> list[dict]:
    if STAYS_FILE.exists():
        import json

        return json.loads(STAYS_FILE.read_text())
    return []


@lru_cache(maxsize=1)
def get_los_model():
    """Lazy-train the RandomForest LOS regressor. Returns None if no data."""
    from sklearn.ensemble import RandomForestRegressor

    stays = [s for s in _load_training_data() if s.get("actual_los_hours")]
    if len(stays) < 20:
        return None

    X = np.array([_featurize(s) for s in stays])
    y = np.array([s["actual_los_hours"] for s in stays])

    model = RandomForestRegressor(n_estimators=120, max_depth=8, random_state=42)
    model.fit(X, y)

    # Feature importance for interpretability
    importances = {
        name: round(float(imp), 3)
        for name, imp in zip(
            ["esi", "bed_type", "isolation", "admit_hour", "admit_dow", "outbreak"],
            model.feature_importances_,
        )
    }
    model.feature_importances_dict = importances  # type: ignore[attr-defined]
    return model


def predict_los(boarder: dict[str, Any]) -> dict[str, Any] | None:
    """Predicts LOS hours for an incoming ER boarder.

    Returns None when the model is untrained (no stay data yet).
    """
    model = get_los_model()
    if model is None:
        return None

    features = np.array([_featurize({**boarder, "admit_hour": 12, "admit_dow": 0, "outbreak_intensity": 0.4})])
    prediction = float(np.clip(model.predict(features)[0], 4.0, 240.0))

    return {
        "predicted_los_hours": round(prediction, 1),
        "model": "random_forest_v1",
        "top_factors": dict(
            sorted(model.feature_importances_dict.items(), key=lambda kv: kv[1], reverse=True)[:3]  # type: ignore[attr-defined]
        ),
    }
