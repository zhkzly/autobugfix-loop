from __future__ import annotations


def test_official_swebench_version_is_locked() -> None:
    import swebench

    assert swebench.__version__ == "4.1.0"
