import asyncio

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
)
from temporalio.service import RPCError
from temporalio.worker import Worker

from app.config import TEMPORAL_HOST, TEMPORAL_NAMESPACE, TEMPORAL_TASK_QUEUE
from app.temporal.activities import run_agent
from app.temporal.accuracy_workflow import (
    ForecastAccuracyWorkflow,
    run_forecast_accuracy_activity,
)
from app.temporal.scheduled_workflow import (
    DailyForecastWorkflow,
    MonthlyForecastWorkflow,
    WeeklyForecastWorkflow,
    run_daily_forecast_activity,
    run_monthly_forecast_activity,
    run_weekly_forecast_activity,
)
from app.temporal.workflows import HospitalCapacityWorkflow

SCHEDULE_DEFINITIONS = [
    (
        "daily-forecast-9am",
        DailyForecastWorkflow,
        run_daily_forecast_activity,
        "0 9 * * *",
    ),
    (
        "weekly-forecast-mon-8am",
        WeeklyForecastWorkflow,
        run_weekly_forecast_activity,
        "0 8 * * 1",
    ),
    (
        "monthly-forecast-1st-8am",
        MonthlyForecastWorkflow,
        run_monthly_forecast_activity,
        "0 8 1 * *",
    ),
    (
        "nightly-accuracy-2350",
        ForecastAccuracyWorkflow,
        run_forecast_accuracy_activity,
        "50 23 * * *",
    ),
]


async def _ensure_schedules(client: Client) -> None:
    """Idempotently registers the cron schedules for the forecast workflows."""
    for schedule_id, workflow_cls, activity_fn, cron in SCHEDULE_DEFINITIONS:
        try:
            await client.create_schedule(
                schedule_id,
                Schedule(
                    action=ScheduleActionStartWorkflow(
                        workflow_cls.run,
                        args=[],
                        id=f"{schedule_id}-workflow",
                        task_queue=TEMPORAL_TASK_QUEUE,
                    ),
                    spec=ScheduleSpec(cron_expressions=[cron]),
                ),
            )
            print(f"[✓] Created schedule '{schedule_id}' (cron: {cron})")
        except (ScheduleAlreadyRunningError, RPCError) as e:
            already_running = isinstance(e, ScheduleAlreadyRunningError) or (
                getattr(e, "status", None) == 6
                or "already exists" in str(e).lower()
                or "already running" in str(e).lower()
            )
            if not already_running:
                raise
            print(f"[*] Schedule '{schedule_id}' already exists — skipping creation.")


async def run_worker() -> None:
    """Connect to Temporal and start the worker."""
    client = await Client.connect(TEMPORAL_HOST, namespace=TEMPORAL_NAMESPACE)

    await _ensure_schedules(client)

    worker = Worker(
        client,
        task_queue=TEMPORAL_TASK_QUEUE,
        workflows=[
            HospitalCapacityWorkflow,
            DailyForecastWorkflow,
            WeeklyForecastWorkflow,
            MonthlyForecastWorkflow,
            ForecastAccuracyWorkflow,
        ],
        activities=[
            run_agent,
            run_daily_forecast_activity,
            run_weekly_forecast_activity,
            run_monthly_forecast_activity,
            run_forecast_accuracy_activity,
        ],
    )

    print(f"[*] Temporal Worker started.")
    print(f"    Host: {TEMPORAL_HOST}")
    print(f"    Namespace: {TEMPORAL_NAMESPACE}")
    print(f"    Task Queue: {TEMPORAL_TASK_QUEUE}")
    print(
        "    Workflows: [HospitalCapacityWorkflow, DailyForecastWorkflow,"
        " WeeklyForecastWorkflow, MonthlyForecastWorkflow]"
    )
    print("    Activities: [run_agent, run_daily/weekly/monthly_forecast_activity]")
    print(f"[*] Listening for tasks...")

    await worker.run()


def main() -> None:
    """Entry point for the Temporal worker."""
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
