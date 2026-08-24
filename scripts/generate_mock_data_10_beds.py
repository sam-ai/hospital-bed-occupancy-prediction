"""CLI for scenario-driven hospital mock data generation.

Thin wrapper around app/data/mock_regimes.py — the engine lives there so the
FastAPI gateway can also generate regimes at runtime (POST /api/mock/regenerate).

Usage:
    python scripts/generate_mock_data_10_beds.py --scenario high_capacity
    python scripts/generate_mock_data_10_beds.py --scenario volatile --days 14 --seed 7
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.data.mock_regimes import SCENARIOS, generate_scenario_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Scenario-driven hospital mock data generator")
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="outbreak_surge")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output", type=str, default=None)
    args = parser.parse_args()

    cfg = SCENARIOS[args.scenario]
    print(f"[*] Generating {args.days} days of mock data — scenario: {args.scenario}")
    print(f"    {cfg.description}")

    dataset = generate_scenario_data(scenario=args.scenario, days=args.days, seed=args.seed)

    out_dir = Path(__file__).parent.parent / "data"
    out_dir.mkdir(exist_ok=True)
    out_file = Path(args.output) if args.output else out_dir / "hospital_30day_mock_data_10_beds.json"
    with open(out_file, "w") as f:
        json.dump([s.model_dump() for s in dataset], f, indent=2)

    occ = [s.census.occupied_beds for s in dataset]
    mean_occ = sum(occ) / len(occ)
    print(f"[OK] {len(dataset)} records → {out_file}")
    print(f"     Occupancy: min {min(occ)} / mean {mean_occ:.1f} / max {max(occ)} "
          f"(band {cfg.occupancy_floor}-{cfg.occupancy_ceiling})")


if __name__ == "__main__":
    main()
