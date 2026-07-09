from autobugfix.locks import FileLock, LockError


def test_file_lock_exclusive(tmp_path):
    path = tmp_path / "lock"
    lock = FileLock(path)
    lock.acquire()
    try:
        try:
            FileLock(path).acquire()
            assert False
        except LockError:
            assert True
    finally:
        lock.release()
    assert not path.exists()
