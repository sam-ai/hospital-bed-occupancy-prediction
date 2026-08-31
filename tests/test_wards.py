"""Tests for multi-ward registry and patient-flow source breakdown."""

from app.data.wards import DEFAULT_UNIT_ID, WARDS, WARDS_BY_ID, get_ward
from app.forecasting.flow_service import _daily_flows, _split_admission_sources


class TestWardRegistry:
    def test_four_wards_defined(self):
        assert len(WARDS) == 4
        ids = {w.unit_id for w in WARDS}
        assert ids == {"ICU-EAST", "GENERAL-MALE", "GENERAL-FEMALE", "STEP-DOWN"}

    def test_all_fixed_ten_beds(self):
        assert all(w.total_beds == 10 for w in WARDS)

    def test_lookup_and_fallback(self):
        assert get_ward("GENERAL-MALE").unit_type == "MED_SURG"
        # Unknown unit falls back to default ward
        assert get_ward("NOPE").unit_id == DEFAULT_UNIT_ID
        assert WARDS_BY_ID[DEFAULT_UNIT_ID].unit_type == "ICU"

    def test_admission_mix_sums_to_one(self):
        for w in WARDS:
            total = w.er_admit_weight + w.elective_weight + w.transfer_in_weight
            assert abs(total - 1.0) < 1e-9, f"{w.unit_id} mix sums to {total}"

    def test_step_down_is_transfer_dominant(self):
        sd = WARDS_BY_ID["STEP-DOWN"]
        assert sd.transfer_in_weight > sd.er_admit_weight

    def test_icu_is_er_dominant(self):
        icu = WARDS_BY_ID["ICU-EAST"]
        assert icu.er_admit_weight > icu.transfer_in_weight


class TestSplitAdmissionSources:
    def test_sources_sum_to_total(self):
        for unit in WARDS_BY_ID:
            for total in range(0, 8):
                split = _split_admission_sources(total, unit)
                assert sum(split.values()) == total

    def test_zero_total(self):
        assert _split_admission_sources(0, "ICU-EAST") == {
            "er_direct": 0, "elective": 0, "icu_transfers": 0,
        }

    def test_unknown_unit_uses_default_mix(self):
        # Default ward = ICU-EAST; same input must give same split
        assert _split_admission_sources(5, "UNKNOWN") == _split_admission_sources(5, "ICU-EAST")


class TestDailyFlows:
    def test_intra_day_churn_captured(self):
        snaps = []
        base_ts = "2026-08-20T00:00:00+00:00"
        # Day: occupancy goes 5 -> 6 -> 4 -> 5 (+1, -2, +1 => 2 adm, 2 dis)
        occs = [5, 6, 4, 5]
        from datetime import datetime, timedelta
        t0 = datetime.fromisoformat(base_ts)
        for i, occ in enumerate(occs):
            snaps.append({
                "timestamp": (t0 + timedelta(hours=i)).isoformat(),
                "census": {"occupied_beds": occ},
            })
        adm, dis, labels = _daily_flows(snaps)
        assert len(labels) == 1
        assert adm[0] == 2.0
        assert dis[0] == 2.0
