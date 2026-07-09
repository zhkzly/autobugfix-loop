from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from autobugfix.config import load_config
from autobugfix.memory.service import MemoryService
from autobugfix.models import utc_now


def worker_paths(project_root: Path) -> dict[str, Path]:
    root = project_root / ".autobugfix-memory"
    return {
        "pid": root / "worker.pid",
        "heartbeat": root / "worker-heartbeat.json",
        "events": root / "worker-events.jsonl",
        "log": root / "worker.log",
    }


def worker_status(project_root: Path) -> dict[str, object]:
    paths = worker_paths(project_root)
    status: dict[str, object] = {"running": False, "pid": None}
    if paths["pid"].exists():
        pid = int(paths["pid"].read_text(encoding="utf-8").strip())
        status["pid"] = pid
        try:
            os.kill(pid, 0)
            status["running"] = True
        except OSError:
            status["running"] = False
    if paths["heartbeat"].exists():
        status["heartbeat"] = json.loads(paths["heartbeat"].read_text(encoding="utf-8"))
    return status


def start_worker(project_root: Path) -> int:
    status = worker_status(project_root)
    if status.get("running"):
        return int(status["pid"])
    paths = worker_paths(project_root)
    paths["pid"].parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "autobugfix.memory_worker", "--loop", str(project_root)],
        stdout=paths["log"].open("ab"),
        stderr=subprocess.STDOUT,
    )
    paths["pid"].write_text(str(proc.pid), encoding="utf-8")
    return proc.pid


def stop_worker(project_root: Path) -> None:
    status = worker_status(project_root)
    pid = status.get("pid")
    if pid:
        os.kill(int(pid), signal.SIGTERM)
    paths = worker_paths(project_root)
    if paths["pid"].exists():
        paths["pid"].unlink()


def loop(project_root: Path) -> None:
    execution_config = load_config(project_root)
    service = MemoryService(project_root)
    service.init()
    paths = worker_paths(project_root)
    while True:
        processed = service.tick(1)
        paths["heartbeat"].write_text(json.dumps({"timestamp": utc_now(), "processed": processed}, sort_keys=True), encoding="utf-8")
        with paths["events"].open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"timestamp": utc_now(), "processed": processed}, sort_keys=True) + "\n")
        time.sleep(execution_config.memory_worker.tick_interval_seconds)


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--loop":
        loop(Path(sys.argv[2]).resolve())
    else:
        print(worker_status(Path.cwd()))


if __name__ == "__main__":
    main()
