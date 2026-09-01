"""Elasticsearch client for hospital snapshot retrieval and forecast persistence.

Indexes:
- hospital-snapshots:        Raw 4-pillar hourly snapshots (written by ingest script)
- hospital-features:         Extracted TimesFM feature matrices (per forecast run)
- hospital-forecast-timeline: Multi-horizon forecast points (24H / 7D / 6M) for UI charts
"""

import os
from datetime import datetime, timezone

import numpy as np
from elasticsearch import AsyncElasticsearch

ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
SNAPSHOTS_INDEX = "hospital-snapshots"
FEATURES_INDEX = "hospital-features"
FORECAST_INDEX = "hospital-forecast-timeline"
ACCURACY_INDEX = "hospital-forecast-accuracy"

es_client = AsyncElasticsearch(
    ELASTICSEARCH_URL,
    request_timeout=10,
    max_retries=3,
    retry_on_timeout=True,
)

FORECAST_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "forecast_date": {"type": "date"},
            "generated_at": {"type": "date"},
            "hospital_id": {"type": "keyword"},
            "unit_id": {"type": "keyword"},
            "horizon_type": {"type": "keyword"},
            "time_step_index": {"type": "integer"},
            "timestamp": {"type": "date"},
            "predicted_occupancy": {"type": "float"},
            "predicted_occupied_beds": {"type": "integer"},
            "peak_occupancy": {"type": "float"},
            "lower_bound": {"type": "float"},
            "upper_bound": {"type": "float"},
            "has_anomaly": {"type": "boolean"},
            "anomaly_severity": {"type": "keyword"},
            "anomaly_type": {"type": "keyword"},
            "anomaly_explanation": {"type": "text"},
        }
    }
}


async def _ensure_index(index: str, mapping: dict | None = None) -> None:
    """Creates the index with explicit mapping if missing (auto-create is disabled)."""
    exists = await es_client.indices.exists(index=index)
    if not exists:
        await es_client.indices.create(index=index, **({"body": mapping} if mapping else {}))


# ============================================================================
# 1. FETCH HISTORICAL SNAPSHOTS FROM ELASTICSEARCH
# ============================================================================
async def fetch_snapshots_from_elasticsearch(
    hospital_id: str, unit_id: str, limit: int = 48, as_of: str | None = None
) -> list[dict]:
    """Retrieves the last N historical snapshots from 'hospital-snapshots' ES index.

    When `as_of` (ISO date/datetime) is provided, only snapshots with
    timestamp < as_of are considered, so the context window ends at a past
    point in time (used for historical-anchor / back-dated forecasts).

    Returns snapshots in chronological order (oldest -> newest).
    """
    must = [
        {"term": {"hospital_id": hospital_id}},
        {"term": {"census.unit_id": unit_id}},
    ]
    if as_of:
        must.append({"range": {"timestamp": {"lt": as_of}}})

    query = {
        "size": limit,
        "query": {"bool": {"must": must}},
        "sort": [{"timestamp": {"order": "desc"}}],
    }

    try:
        response = await es_client.search(index=SNAPSHOTS_INDEX, body=query)
        hits = response["hits"]["hits"]
        snapshots = [hit["_source"] for hit in hits]
        snapshots.reverse()
        return snapshots
    except Exception as e:
        print(f"[!] Warning: Could not fetch snapshots from ES ({e}). Returning empty list.")
        return []


# ============================================================================
# 2. SAVE EXTRACTED FEATURES TO ELASTICSEARCH
# ============================================================================
async def save_features_to_elasticsearch(
    hospital_id: str,
    unit_id: str,
    features: dict[str, np.ndarray],
) -> str:
    """Indexes extracted TimesFM feature matrices into 'hospital-features' index."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    doc_id = f"FEAT_{today_str}_{hospital_id}_{unit_id}"

    await _ensure_index(FEATURES_INDEX)

    doc = {
        "forecast_date": today_str,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "hospital_id": hospital_id,
        "unit_id": unit_id,
        "past_target": features["past_target"].tolist(),
        "past_covariates": features["past_covariates"].tolist(),
        "future_covariates": features["future_covariates"].tolist(),
        "static_covariates": features["static_covariates"].tolist(),
    }

    await es_client.index(index=FEATURES_INDEX, id=doc_id, document=doc)
    print(f"[✓] Saved extracted features to '{FEATURES_INDEX}' (ID: {doc_id})")
    return doc_id


# ============================================================================
# 3. SAVE MULTI-HORIZON FORECASTS (24H / 7D / 6M) TO ELASTICSEARCH
# ============================================================================
# ============================================================================
# 4. FORECAST ACCURACY (strategy loop)
# ============================================================================
ACCURACY_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "day": {"type": "date"},
            "horizon_type": {"type": "keyword"},
            "hospital_id": {"type": "keyword"},
            "unit_id": {"type": "keyword"},
            "mae": {"type": "float"},
            "bias": {"type": "float"},
            "points_evaluated": {"type": "integer"},
            "evaluated_at": {"type": "date"},
        }
    }
}


async def save_accuracy_record(record: dict) -> str:
    """Stores one per-day forecast accuracy record."""
    await _ensure_index(ACCURACY_INDEX, ACCURACY_INDEX_MAPPING)
    day = record.get("day", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    doc_id = (
        f"ACC_{day}_{record.get('horizon_type', '24H')}"
        f"_{record.get('hospital_id', 'X')}_"
        f"{record.get('unit_id', 'ICU-EAST')}"
    )
    await es_client.index(index=ACCURACY_INDEX, id=doc_id, document=record)
    return doc_id


async def fetch_accuracy_records(
    hospital_id: str,
    unit_id: str,
    horizon_type: str = "24H",
    days: int = 7,
) -> list[dict]:
    """Fetches the most recent N daily accuracy records, newest first."""
    try:
        response = await es_client.search(
            index=ACCURACY_INDEX,
            body={
                "size": days,
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"hospital_id": hospital_id}},
                            {"term": {"unit_id": unit_id}},
                            {"term": {"horizon_type": horizon_type}},
                        ]
                    }
                },
                "sort": [{"day": {"order": "desc"}}],
            },
        )
        return [hit["_source"] for hit in response["hits"]["hits"]]
    except Exception as e:
        print(f"[!] Accuracy query failed ({e}). Returning empty list.")
        return []


# ============================================================================
# 3b. PATIENT FLOW (daily admissions/discharges forecast + source breakdown)
# ============================================================================
PATIENT_FLOW_INDEX = "hospital-patient-flow"

PATIENT_FLOW_INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "forecast_date": {"type": "date"},
            "generated_at": {"type": "date"},
            "hospital_id": {"type": "keyword"},
            "unit_id": {"type": "keyword"},
            "predicted_admissions": {"type": "integer"},
            "er_direct": {"type": "integer"},
            "elective": {"type": "integer"},
            "icu_transfers": {"type": "integer"},
            "predicted_discharges": {"type": "integer"},
            "baseline_admissions_90d": {"type": "float"},
            "baseline_discharges_90d": {"type": "float"},
        }
    }
}


async def save_patient_flow_record(record: dict) -> str:
    """Stores one per-day patient flow forecast record."""
    await _ensure_index(PATIENT_FLOW_INDEX, PATIENT_FLOW_INDEX_MAPPING)
    doc_id = (
        f"FLOW_{record.get('forecast_date', 'X')}_{record.get('hospital_id', 'X')}"
        f"_{record.get('unit_id', 'X')}"
    )
    await es_client.index(index=PATIENT_FLOW_INDEX, id=doc_id, document=record)
    return doc_id


def _anomaly_map_by_step(anomaly_result: dict) -> dict[int, dict]:
    """Maps anomaly alerts by 1-based hour offset (affected_hour_offset is 0-based)."""
    return {
        alert.get("affected_hour_offset", 0) + 1: alert
        for alert in anomaly_result.get("alerts", [])
    }


async def index_multi_horizon_forecasts_to_elasticsearch(
    hospital_id: str,
    unit_id: str,
    horizon_type: str,
    forecast_points: list[dict],
    total_beds: int,
    anomaly_result: dict | None = None,
    forecast_date: str | None = None,
) -> int:
    """Stores forecast points for a single horizon ('24H', '7D', or '6M') in ES.

    Each point dict requires: timestamp, predicted_occupancy, lower_bound,
    upper_bound. Optional: peak_occupancy. predicted_occupied_beds is derived
    from predicted_occupancy * total_beds.

    `forecast_date` (YYYY-MM-DD) overrides the stamped/keyed date so callers can
    persist back-dated (historical-anchor) forecasts without overwriting the
    live "today" forecast. Defaults to the current UTC date.

    Returns the number of indexed documents.
    """
    today_str = forecast_date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    anomaly_map = _anomaly_map_by_step(anomaly_result or {})

    await _ensure_index(FORECAST_INDEX, FORECAST_INDEX_MAPPING)

    indexed = 0
    for idx, pt in enumerate(forecast_points):
        time_step = idx + 1
        alert = anomaly_map.get(time_step) if horizon_type == "24H" else None

        pred_occ = float(pt["predicted_occupancy"])
        # Guard against NaN/inf in model output: clip to [0, 1] occupancy range.
        if not (pred_occ == pred_occ):  # NaN check (NaN != NaN)
            pred_occ = 0.0
        pred_occ = max(0.0, min(1.0, pred_occ))

        lower_val = float(pt["lower_bound"])
        upper_val = float(pt["upper_bound"])
        peak_val = float(pt.get("peak_occupancy", pred_occ))
        # Coerce NaN/inf in bounds to safe fallbacks
        if not (lower_val == lower_val):  # NaN
            lower_val = pred_occ
        if not (upper_val == upper_val):  # NaN
            upper_val = pred_occ
        if not (peak_val == peak_val):  # NaN
            peak_val = pred_occ

        doc = {
            "forecast_date": today_str,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "hospital_id": hospital_id,
            "unit_id": unit_id,
            "horizon_type": horizon_type,
            "time_step_index": time_step,
            "timestamp": pt["timestamp"],
            "predicted_occupancy": round(pred_occ, 4),
            "predicted_occupied_beds": max(0, min(total_beds, int(round(pred_occ * total_beds)))),
            "peak_occupancy": round(peak_val, 4),
            "lower_bound": round(lower_val, 4),
            "upper_bound": round(upper_val, 4),
            "has_anomaly": alert is not None,
            "anomaly_severity": alert["severity"] if alert else "none",
            "anomaly_type": alert["anomaly_type"] if alert else None,
            "anomaly_explanation": alert["explanation"] if alert else None,
        }

        doc_id = f"{today_str}_{hospital_id}_{unit_id}_{horizon_type}_STEP{time_step:03d}"
        await es_client.index(index=FORECAST_INDEX, id=doc_id, document=doc)
        indexed += 1

    print(
        f"[✓] Indexed {indexed} '{horizon_type}' forecast points into '{FORECAST_INDEX}'"
    )
    return indexed
