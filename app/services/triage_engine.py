"""Clinical Triage & Fast-Track Bed Matching Engine.

Deterministically calculates patient priority scores using standard clinical
acuity protocols and matches highest-acuity ER boarders to available beds
or generates expedited discharge/EVS cleaning triggers.

Priority Formula:
    Score = (6 - ESI) * 100 + NEWS2 * 10 + (Wait_Minutes / 10) + Isolation_Bonus

An LLM must NEVER do medical triage ranking alone — this is deterministic.
"""

from typing import Any

from app.models.triage import BedAllocationMatch, WaitingPatient


class TriageEngine:
    """Calculates clinical priority scores and matches ER boarders to beds.

    Ranking Logic:
        1. ESI Level (inverse): ESI 1 = highest priority (+500), ESI 5 = lowest (+100)
        2. NEWS2 Score: Higher NEWS2 = sicker patient (weighted x10)
        3. Wait Time: Longer waits increase priority (weighted /10)
        4. Isolation: Isolation patients get +50 bonus (harder to place)

    Bed Matching Priority:
        1. Clean beds matching required type → READY_TO_ASSIGN
        2. Dirty beds matching type → AWAITING_EVS_CLEANING (trigger STAT clean)
        3. Pending discharges matching type → NEEDS_EXPEDITED_DISCHARGE
    """

    @staticmethod
    def calculate_priority_score(patient: WaitingPatient) -> float:
        """Calculate deterministic clinical priority score.

        Higher score = higher urgency = first in line for bed assignment.
        """
        esi_score = (6 - patient.esi_level) * 100.0
        news_score = patient.news2_score * 10.0
        wait_score = patient.wait_time_minutes / 10.0
        isolation_bonus = 50.0 if patient.isolation_required else 0.0

        return esi_score + news_score + wait_score + isolation_bonus

    def rank_and_match_patients(
        self,
        waiting_patients: list[WaitingPatient],
        available_beds: list[dict[str, Any]],
        pending_discharges: list[dict[str, Any]],
    ) -> list[BedAllocationMatch]:
        """Rank patients by acuity and match to best available bed.

        Args:
            waiting_patients: List of ER boarders waiting for beds.
            available_beds: List of bed dicts with bed_id, type, status (CLEAN/DIRTY).
            pending_discharges: List of bed dicts where patient is leaving today.

        Returns:
            Ordered list of BedAllocationMatch (highest priority first).
        """
        # 1. Rank patients by priority score (descending)
        sorted_patients = sorted(
            waiting_patients,
            key=lambda p: self.calculate_priority_score(p),
            reverse=True,
        )

        matches: list[BedAllocationMatch] = []
        unassigned_clean_beds = [b for b in available_beds if b.get("status") == "CLEAN"]
        unassigned_dirty_beds = [b for b in available_beds if b.get("status") == "DIRTY"]
        remaining_discharges = list(pending_discharges)

        for patient in sorted_patients:
            score = self.calculate_priority_score(patient)

            # Priority 1: Match with currently available CLEAN bed
            matching_clean = next(
                (b for b in unassigned_clean_beds if b["type"] == patient.required_bed_type),
                None,
            )

            if matching_clean:
                unassigned_clean_beds.remove(matching_clean)
                matches.append(
                    BedAllocationMatch(
                        patient_id=patient.patient_id,
                        mrn=patient.mrn,
                        esi_level=patient.esi_level,
                        priority_score=round(score, 1),
                        matched_bed_id=matching_clean["bed_id"],
                        allocation_status="READY_TO_ASSIGN",
                        action_item=f"Immediate assign to {matching_clean['bed_id']} ({matching_clean['type']}).",
                    )
                )
                continue

            # Priority 2: Match with DIRTY bed (trigger STAT EVS cleaning)
            matching_dirty = next(
                (b for b in unassigned_dirty_beds if b["type"] == patient.required_bed_type),
                None,
            )

            if matching_dirty:
                unassigned_dirty_beds.remove(matching_dirty)
                matches.append(
                    BedAllocationMatch(
                        patient_id=patient.patient_id,
                        mrn=patient.mrn,
                        esi_level=patient.esi_level,
                        priority_score=round(score, 1),
                        matched_bed_id=matching_dirty["bed_id"],
                        allocation_status="AWAITING_EVS_CLEANING",
                        action_item=f"Trigger STAT EVS cleaning for {matching_dirty['bed_id']}.",
                    )
                )
                continue

            # Priority 3: Match with pending discharge (expedite)
            matching_discharge = next(
                (d for d in remaining_discharges if d["type"] == patient.required_bed_type),
                None,
            )

            if matching_discharge:
                remaining_discharges.remove(matching_discharge)
                matches.append(
                    BedAllocationMatch(
                        patient_id=patient.patient_id,
                        mrn=patient.mrn,
                        esi_level=patient.esi_level,
                        priority_score=round(score, 1),
                        matched_bed_id=matching_discharge["bed_id"],
                        allocation_status="NEEDS_EXPEDITED_DISCHARGE",
                        action_item=f"Request STAT physician discharge sign-off for {matching_discharge['bed_id']}.",
                    )
                )
            else:
                # No matching bed found — escalate
                matches.append(
                    BedAllocationMatch(
                        patient_id=patient.patient_id,
                        mrn=patient.mrn,
                        esi_level=patient.esi_level,
                        priority_score=round(score, 1),
                        matched_bed_id=None,
                        allocation_status="NEEDS_EXPEDITED_DISCHARGE",
                        action_item="Request STAT physician discharge sign-off for Floor Unit.",
                    )
                )

        return matches
