from pathlib import Path

import pytest

from autobugfix.models import PpeConfig, RepoProfile
from autobugfix.ppe import PpeError, deploy_ppe


def test_ppe_disabled_fails_clearly(tmp_path):
    repo = RepoProfile(repo_id="toy", main_checkout=tmp_path, ppe=PpeConfig(enabled=False))
    with pytest.raises(PpeError):
        deploy_ppe(repo, Path(tmp_path), "t1")
