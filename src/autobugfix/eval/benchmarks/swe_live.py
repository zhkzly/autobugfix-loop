from __future__ import annotations

from autobugfix.eval.benchmarks.swe_official import SWEOfficialRunner


class SWELiveAdapter(SWEOfficialRunner):
    def __init__(self, runtime):
        super().__init__(runtime, "swebench_live")
