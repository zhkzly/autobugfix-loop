from autobugfix.memory_worker import worker_status


def test_memory_worker_status_when_not_running(tmp_path):
    assert worker_status(tmp_path)["running"] is False
