from datetime import timedelta

from temporalio import workflow

with workflow.unsafe.imports_passed_through():
    from app.models import AgentResult, ExecutionReport, HospitalRequest
    from app.temporal.activities import execute_approved_recommendation, run_agent


@workflow.defn
class HospitalCapacityWorkflow:
    """Durable workflow for hospital capacity assessment with human-in-the-loop approval.

    Flow:
        1. Execute the LangGraph multi-agent pipeline via activity
        2. If policy requires HUMAN_APPROVAL, pause and wait for signal
        3. Return final result with approval status
    """

    def __init__(self) -> None:
        self._human_approved: bool | None = None
        self._phase: str = "RUNNING"
        self._result: AgentResult | None = None

    @workflow.signal
    def approve_recommendation(self, approved: bool) -> None:
        """Signal handler for human approval/rejection of recommendations."""
        self._human_approved = approved

    @workflow.query
    def status(self) -> dict:
        """Current workflow phase plus pending policy/recommendation data."""
        return {
            "phase": self._phase,
            "policy_decision": (
                self._result.policy_decision.model_dump()
                if self._result and self._result.policy_decision
                else None
            ),
            "recommendations": (
                [r.model_dump() for r in self._result.recommendations]
                if self._result
                else []
            ),
        }

    @workflow.run
    async def run(self, request: HospitalRequest) -> AgentResult:
        workflow.logger.info("Starting Workflow ID: %s", request.request_id)

        # Execute LangGraph Multi-Agent Activity
        result: AgentResult = await workflow.execute_activity(
            run_agent,
            request,
            start_to_close_timeout=timedelta(minutes=5),
        )
        self._result = result

        # Check Policy Decision for Human-in-the-Loop
        if (
            result.policy_decision
            and result.policy_decision.decision == "HUMAN_APPROVAL"
        ):
            self._phase = "AWAITING_APPROVAL"
            workflow.logger.warn(
                "Policy requires human approval for Workflow %s. Awaiting signal...",
                request.request_id,
            )

            # Pause workflow safely — no resources consumed while waiting
            try:
                await workflow.wait_condition(
                    lambda: self._human_approved is not None,
                    timeout=timedelta(hours=24),
                )
                if self._human_approved:
                    workflow.logger.info(
                        "Recommendation APPROVED by human operator."
                    )
                    result.recommendation_summary += " [STATUS: APPROVED BY HUMAN]"

                    # Execute the approved recommendation (staff alert dispatch)
                    result.execution_report = await workflow.execute_activity(
                        execute_approved_recommendation,
                        args=[result, True],
                        start_to_close_timeout=timedelta(minutes=1),
                    )
                else:
                    workflow.logger.info(
                        "Recommendation REJECTED by human operator."
                    )
                    result.recommendation_summary += " [STATUS: REJECTED BY HUMAN]"
                    result.execution_report = ExecutionReport(
                        status="REJECTED",
                        dispatched_at=workflow.now().isoformat(),
                    )
            except TimeoutError:
                workflow.logger.error(
                    "Human approval timed out after 24 hours."
                )
                result.recommendation_summary += " [STATUS: APPROVAL TIMED OUT]"
                result.execution_report = ExecutionReport(
                    status="SKIPPED",
                    dispatched_at=workflow.now().isoformat(),
                )

        self._phase = "COMPLETED"
        return result
