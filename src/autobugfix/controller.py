from __future__ import annotations

from autobugfix.projection import inspect_projection, status_projection
from autobugfix.scheduler import tick
from autobugfix.service import AutobugfixService


class Controller:
    def __init__(self, service: AutobugfixService) -> None:
        self.service = service

    def status(self) -> dict[str, object]:
        return status_projection(self.service.store)

    def inspect(self, task_id: str) -> dict[str, object]:
        return inspect_projection(self.service.store, task_id)

    def tick(self, max_concurrent: int | None = None) -> list[str]:
        return tick(self.service, max_concurrent)
