from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

from autobugfix.git_utils import run_git
from autobugfix.worktree import git_diff_with_untracked


def _git(path: Path, *args: str) -> None:
    result = subprocess.run(
        ["git", "-C", str(path), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_binary_untracked_file_diff_does_not_crash_and_is_byte_faithful(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    payload = bytes(
        [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0xAC, 0xFF]
    )
    (repo / "fixture.png").write_bytes(payload)

    diff = git_diff_with_untracked(repo, "HEAD")

    assert "fixture.png" in diff
    encoded = diff.encode("utf-8", errors="surrogateescape")
    assert b"GIT binary patch" in encoded
    assert hashlib.sha256(encoded).hexdigest()


def test_run_git_decodes_arbitrary_bytes_with_surrogateescape(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    (repo / "blob.bin").write_bytes(b"\xac\xed\x00")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    result = run_git(
        repo,
        ["diff", "--binary", "--no-index", "--", "/dev/null", "blob.bin"],
        check=False,
    )

    assert result.returncode == 1
    assert "blob.bin" in result.stdout
