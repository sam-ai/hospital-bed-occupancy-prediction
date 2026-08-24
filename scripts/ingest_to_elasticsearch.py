"""Ingest Hospital Snapshot data into Elasticsearch.

Features:
- Initializes index 'hospital-snapshots' with strict time-series mappings.
- Supports single JSON payload or bulk file ingestion.
- Deterministic Document IDs prevent duplicate entries.

Usage:
    python scripts/ingest_to_elasticsearch.py
"""

import asyncio
import json
import os
from typing import Any
from elasticsearch import AsyncElasticsearch, helpers

# Configuration
ELASTICSEARCH_URL = os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
INDEX_NAME = "hospital-snapshots"

# ============================================================================
# ELASTICSEARCH MAPPING MAPPINGS
# ============================================================================
INDEX_MAPPING = {
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"},
            "hospital_id": {"type": "keyword"},
            "census": {
                "properties": {
                    "unit_id": {"type": "keyword"},
                    "unit_type": {"type": "keyword"},
                    "total_beds": {"type": "integer"},
                    "occupied_beds": {"type": "integer"},
                    "blocked_beds": {"type": "integer"},
                    "admissions_24h": {"type": "integer"},
                    "discharges_24h": {"type": "integer"},
                    "pending_discharges_today": {"type": "integer"},
                    "staff_on_duty": {"type": "integer"},
                    "average_los_hours": {"type": "float"},
                    "bed_turnover_time_hours": {"type": "float"},
                }
            },
            "er_arrivals": {
                "properties": {
                    "er_current_waiting_count": {"type": "integer"},
                    "er_admit_decisions_pending": {"type": "integer"},
                    "er_high_acuity_arrivals_last_6h": {"type": "integer"},
                }
            },
            "scheduled_cases": {
                "properties": {
                    "scheduled_elective_admissions_24h": {"type": "integer"},
                    "scheduled_post_op_icu_beds": {"type": "integer"},
                    "same_day_surgeries_count": {"type": "integer"},
                }
            },
            "external_signals": {
                "type": "nested",
                "properties": {
                    "signal_type": {"type": "keyword"},
                    "value": {"type": "float"},
                    "direction": {"type": "keyword"},
                    "severity": {"type": "keyword"},
                    "confidence": {"type": "float"},
                },
            },
            "historical_occupancy_48h": {"type": "integer"},
        }
    }
}


# ============================================================================
# INGESTION SERVICE
# ============================================================================
class HospitalElasticsearchIngestor:

    def __init__(self, es_url: str = ELASTICSEARCH_URL):
        self.es = AsyncElasticsearch(es_url)

    async def init_index(self) -> None:
        """Create the index with mappings if it does not already exist."""
        exists = await self.es.indices.exists(index=INDEX_NAME)
        if not exists:
            await self.es.indices.create(index=INDEX_NAME, body=INDEX_MAPPING)
            print(f"[✓] Created index '{INDEX_NAME}' with mappings.")
        else:
            print(f"[*] Index '{INDEX_NAME}' already exists.")

    @staticmethod
    def _generate_doc_id(snapshot: dict[str, Any]) -> str:
        """Generates a deterministic ID to avoid duplicates (e.g. HOSPITAL-MAIN-01_FLOOR-1_2026-07-22T23:35:12)."""
        h_id = snapshot.get("hospital_id", "UNKNOWN")
        u_id = snapshot.get("census", {}).get("unit_id", "UNKNOWN")
        ts = snapshot.get("timestamp", "NO_TS").replace(":", "-").replace(".", "-")
        return f"{h_id}_{u_id}_{ts}"

    async def ingest_single(self, snapshot: dict[str, Any]) -> str:
        """Ingests a single hospital snapshot document."""
        await self.init_index()
        doc_id = self._generate_doc_id(snapshot)

        response = await self.es.index(
            index=INDEX_NAME,
            id=doc_id,
            document=snapshot,
        )
        print(f"[✓] Successfully ingested document ID: {doc_id} (Result: {response['result']})")
        return doc_id

    async def ingest_bulk_file(self, json_file_path: str) -> None:
        """Bulk ingests an array of snapshots from a JSON file."""
        await self.init_index()

        if not os.path.exists(json_file_path):
            print(f"[!] File not found: {json_file_path}")
            return

        with open(json_file_path, "r") as f:
            snapshots = json.load(f)

        actions = [
            {
                "_index": INDEX_NAME,
                "_id": self._generate_doc_id(s),
                "_source": s,
            }
            for s in snapshots
        ]

        success, failed = await helpers.async_bulk(self.es, actions)
        print(f"[✓] Bulk ingestion complete: {success} succeeded, {len(failed)} failed.")

    async def close(self) -> None:
        await self.es.close()


# ============================================================================
# SAMPLE RUNNER
# ============================================================================
SAMPLE_SNAPSHOT = {
    "timestamp": "2026-07-22T23:35:12.514944+00:00",
    "hospital_id": "HOSPITAL-MAIN-01",
    "census": {
        "unit_id": "FLOOR-1",
        "unit_type": "ICU",
        "total_beds": 10,
        "occupied_beds": 6,
        "blocked_beds": 0,
        "admissions_24h": 2,
        "discharges_24h": 3,
        "pending_discharges_today": 1,
        "staff_on_duty": 2,
        "average_los_hours": 42.0,
        "bed_turnover_time_hours": 1.2,
    },
    "er_arrivals": {
        "er_current_waiting_count": 1,
        "er_admit_decisions_pending": 0,
        "er_high_acuity_arrivals_last_6h": 0,
    },
    "scheduled_cases": {
        "scheduled_elective_admissions_24h": 0,
        "scheduled_post_op_icu_beds": 0,
        "same_day_surgeries_count": 1,
    },
    "external_signals": [
        {
            "signal_type": "flu_outbreak_index",
            "value": 0.2,
            "direction": "stable",
            "severity": "low",
            "confidence": 0.91,
        },
        {
            "signal_type": "seasonality_weather",
            "value": 0.3,
            "direction": "stable",
            "severity": "low",
            "confidence": 0.95,
        },
    ],
    "historical_occupancy_48h": [
        5, 5, 7, 6, 5, 5, 5, 7, 5, 7, 7, 7, 5, 7, 6, 5,
        5, 5, 5, 5, 7, 7, 5, 7, 5, 7, 7, 7, 7, 6, 5, 6,
        7, 6, 5, 5, 7, 6, 6, 6, 5, 5, 6, 5, 5, 6, 5, 6,
    ],
}


async def main():
    ingestor = HospitalElasticsearchIngestor()

    print("==========================================================")
    print("       INGESTING HOSPITAL SNAPSHOT TO ELASTICSEARCH        ")
    print("==========================================================")

    # 1. Ingest the sample document provided
    await ingestor.ingest_single(SAMPLE_SNAPSHOT)

    # 2. (Optional) Bulk ingest 30-day mock file if present
    mock_file = "data/hospital_30day_mock_data.json"
    if os.path.exists(mock_file):
        print(f"\n[*] Found bulk dataset at '{mock_file}'. Starting bulk import...")
        await ingestor.ingest_bulk_file(mock_file)

    await ingestor.close()
    print("==========================================================\n")


if __name__ == "__main__":
    asyncio.run(main())