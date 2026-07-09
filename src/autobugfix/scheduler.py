from __future__ import annotations

from autobugfix.models import RUNNABLE_STATES
from autobugfix.service import AutobugfixService


def tick(service: AutobugfixService, max_concurrent: int | None = None) -> list[str]:
    limit = max_concurrent or service.config.scheduler.default_max_concurrent
    run: list[str] = []
    for record in service.store.list_active():
        if len(run) >= limit:
            break
        if record.state in RUNNABLE_STATES:
            service.run_task(record.task_id)
            run.append(record.task_id)
    return run
