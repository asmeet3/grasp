from unittest.mock import Mock

from src.observability import MetricRecorder
from src.sync.scheduler import SyncScheduler


def test_scheduler_accepts_and_records_metrics() -> None:
    orchestrator = Mock()
    orchestrator.is_running = True
    metrics = MetricRecorder()

    scheduler = SyncScheduler(orchestrator=orchestrator, metrics=metrics)
    scheduler._trigger_sync()

    assert metrics.distribution("scheduler.trigger").count == 1
    assert metrics.distribution("scheduler.skipped_running").count == 1
