from app.models import PolicyDecision, Recommendation


class HospitalPolicyEngine:
    """Deterministic safety policy engine for hospital capacity recommendations.

    Rules:
        - No recommendations → ALLOW (no changes needed)
        - Any recommendation with priority critical/high or requires_human_approval → HUMAN_APPROVAL
        - All recommendations low risk and pre-authorized → ALLOW
    """

    POLICY_ID = "SAFETY-POLICY-BED-MGMT"
    POLICY_VERSION = "2.1.0"

    def evaluate(self, recommendations: list[Recommendation]) -> PolicyDecision:
        """Evaluate recommendations against safety policy rules."""
        if not recommendations:
            return PolicyDecision(
                decision="ALLOW",
                reason="No actionable operational changes requested.",
                policy_id=self.POLICY_ID,
                policy_version=self.POLICY_VERSION,
            )

        for rec in recommendations:
            if rec.priority in ["critical", "high"] or rec.requires_human_approval:
                return PolicyDecision(
                    decision="HUMAN_APPROVAL",
                    reason=(
                        f"Recommendation '{rec.title}' has high impact or priority "
                        f"'{rec.priority}' and requires human authorization."
                    ),
                    policy_id=self.POLICY_ID,
                    policy_version=self.POLICY_VERSION,
                )

        return PolicyDecision(
            decision="ALLOW",
            reason="All proposed recommendations are low risk and pre-authorized.",
            policy_id=self.POLICY_ID,
            policy_version=self.POLICY_VERSION,
        )
