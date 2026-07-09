from autobugfix.worker import worker_status


def test_worker_status_when_not_running(tmp_path):
    assert worker_status(tmp_path)["running"] is False
