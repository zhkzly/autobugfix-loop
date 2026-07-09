from __future__ import annotations

from autobugfix.models import RepoProfile


def full_verification_command(repo: RepoProfile) -> str:
    return repo.test_commands.full
