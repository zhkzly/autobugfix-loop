from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from autobugfix.eval.benchmarks.models import digest_payload
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.swe_models import SWEInstance
from autobugfix.eval.benchmarks.swe_official import SWEOfficialRunner
from autobugfix.eval.benchmarks.swe_runtime import SWERuntimeError


@dataclass(slots=True, frozen=True)
class SWEMaterializedRepository:
    instance_id: str
    repository: str
    base_commit: str
    source_path: str
    source_tree: str
    source_digest: str
    image: str
    image_id: str


class SWEImageMaterializer:
    def __init__(self, runner: SWEOfficialRunner):
        self.runner = runner
        self.runtime = runner.runtime

    @staticmethod
    def _git(checkout: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(checkout), *args],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            raise SWERuntimeError(result.stderr.strip() or "Git inspection failed")
        return result.stdout.strip()

    @staticmethod
    def _repo_root(staging: Path) -> Path:
        if (staging / ".git").exists():
            return staging
        candidates = sorted(
            path.parent
            for path in staging.rglob(".git")
            if path.is_dir()
            and not path.is_symlink()
            and len(path.relative_to(staging).parts) <= 3
        )
        if len(candidates) != 1:
            raise SWERuntimeError("official image does not expose one unambiguous Git repository")
        return candidates[0]

    def _require_git_command(
        self,
        argv: list[str],
        *,
        artifact_dir: Path,
        name: str,
        error: str,
    ) -> None:
        evidence = run_command(
            argv,
            cwd=self.runtime.project_root,
            artifact_dir=artifact_dir,
            name=name,
            timeout_seconds=self.runtime.benchmark_config.command_timeout_seconds,
        )
        if evidence.passed:
            return
        detail = Path(evidence.stderr_path).read_text(
            encoding="utf-8", errors="replace"
        ).strip()
        raise SWERuntimeError(f"{error}: {detail}" if detail else error)

    def _clone_base_snapshot(
        self,
        instance: SWEInstance,
        source: Path,
        destination: Path,
        artifact_root: Path,
    ) -> None:
        source_head = self._git(source, "rev-parse", "HEAD")
        # Official images may carry setup/build artifacts in their worktree. The
        # exact base commit object is authoritative; the destination is verified
        # independently after fetching it without copying source worktree files.
        self._require_git_command(
            [
                "git",
                "-C",
                str(source),
                "cat-file",
                "-e",
                f"{instance.base_commit}^{{commit}}",
            ],
            artifact_dir=artifact_root / "git-base-exists",
            name="git-base-exists",
            error="dataset base commit is absent from the official image repository",
        )
        self._require_git_command(
            [
                "git",
                "-C",
                str(source),
                "merge-base",
                "--is-ancestor",
                instance.base_commit,
                source_head,
            ],
            artifact_dir=artifact_root / "git-base-ancestor",
            name="git-base-ancestor",
            error="dataset base commit is not an ancestor of the official image HEAD",
        )
        self._require_git_command(
            ["git", "init", str(destination)],
            artifact_dir=artifact_root / "git-init",
            name="git-init-base-snapshot",
            error="failed to initialize the sanitized source snapshot",
        )
        self._require_git_command(
            [
                "git",
                "-c",
                "protocol.file.allow=always",
                "-C",
                str(destination),
                "fetch",
                "--depth=1",
                "--no-tags",
                source.resolve().as_uri(),
                instance.base_commit,
            ],
            artifact_dir=artifact_root / "git-fetch-base",
            name="git-fetch-dataset-base",
            error="failed to fetch the exact dataset base commit",
        )
        self._require_git_command(
            [
                "git",
                "-C",
                str(destination),
                "checkout",
                "--detach",
                "FETCH_HEAD",
            ],
            artifact_dir=artifact_root / "git-checkout-base",
            name="git-checkout-dataset-base",
            error="failed to check out the dataset base commit",
        )
        (destination / ".git/FETCH_HEAD").unlink(missing_ok=True)
        (destination / ".git/autobugfix-sanitized-v1").write_text(
            instance.base_commit + "\n", encoding="utf-8"
        )

        head = self._git(destination, "rev-parse", "HEAD")
        status = self._git(
            destination,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        refs = self._git(destination, "for-each-ref", "--format=%(refname)")
        remotes = self._git(destination, "remote")
        commits = self._git(destination, "rev-list", "--count", "HEAD")
        ignored = self._git(
            destination,
            "status",
            "--porcelain=v1",
            "--ignored=matching",
        )
        if (
            head != instance.base_commit
            or status
            or refs
            or remotes
            or commits != "1"
            or ignored
        ):
            raise SWERuntimeError("cloned SWE source snapshot failed base identity checks")

    def _verify_existing(
        self,
        instance: SWEInstance,
        destination: Path,
        image_id: str,
    ) -> SWEMaterializedRepository:
        head = self._git(destination, "rev-parse", "HEAD")
        tree = self._git(destination, "rev-parse", "HEAD^{tree}")
        status = self._git(destination, "status", "--porcelain=v1", "--untracked-files=all")
        marker = destination / ".git/autobugfix-sanitized-v1"
        refs = self._git(destination, "for-each-ref", "--format=%(refname)")
        remotes = self._git(destination, "remote")
        commits = self._git(destination, "rev-list", "--count", "HEAD")
        ignored = self._git(
            destination,
            "status",
            "--porcelain=v1",
            "--ignored=matching",
        )
        if (
            head != instance.base_commit
            or status
            or not marker.is_file()
            or marker.read_text(encoding="utf-8").strip() != instance.base_commit
            or refs
            or remotes
            or commits != "1"
            or ignored
        ):
            raise SWERuntimeError("materialized SWE source snapshot drift")
        digest = digest_payload(
            {
                "instance_id": instance.instance_id,
                "repository": instance.repository,
                "base_commit": head,
                "tree": tree,
                "image": instance.docker_image,
                "image_id": image_id,
            }
        )
        return SWEMaterializedRepository(
            instance_id=instance.instance_id,
            repository=instance.repository,
            base_commit=head,
            source_path=str(destination.resolve()),
            source_tree=tree,
            source_digest=digest,
            image=instance.docker_image,
            image_id=image_id,
        )

    def materialize(
        self,
        instance: SWEInstance,
        artifact_root: Path,
    ) -> SWEMaterializedRepository:
        image_id = self.runner.image_id(instance, artifact_root / "image")
        destination = (
            self.runtime.cache_root
            / "sources"
            / instance.adapter
            / instance.instance_id
            / instance.base_commit
        )
        if destination.exists():
            marker = destination / ".git/autobugfix-sanitized-v1"
            if marker.is_file():
                return self._verify_existing(instance, destination, image_id)
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_parent = Path(
            tempfile.mkdtemp(prefix=".materialize-", dir=destination.parent)
        )
        copied_root = staging_parent / "image-testbed"
        snapshot = staging_parent / "base-snapshot"
        copied_root.mkdir()
        container_name = f"autobugfix-materialize-{uuid.uuid4().hex}"
        docker = shutil.which("docker")
        if not docker:
            raise SWERuntimeError("docker executable is unavailable")
        try:
            create = run_command(
                [
                    docker,
                    "create",
                    "--platform",
                    self.runtime.config.platform,
                    "--name",
                    container_name,
                    image_id,
                ],
                cwd=self.runtime.project_root,
                artifact_dir=artifact_root / "docker-create",
                name="docker-create-materializer",
                timeout_seconds=120,
            )
            if not create.passed:
                raise SWERuntimeError("failed to create official materializer container")
            copied = run_command(
                [docker, "cp", f"{container_name}:/testbed/.", str(copied_root)],
                cwd=self.runtime.project_root,
                artifact_dir=artifact_root / "docker-copy",
                name="docker-copy-testbed",
                timeout_seconds=self.runtime.benchmark_config.command_timeout_seconds,
            )
            if not copied.passed:
                raise SWERuntimeError("failed to copy /testbed from official image")
            source = self._repo_root(copied_root)
            self._clone_base_snapshot(
                instance,
                source,
                snapshot,
                artifact_root / "git-snapshot",
            )
            os.replace(snapshot, destination)
        finally:
            run_command(
                [docker, "rm", "-f", container_name],
                cwd=self.runtime.project_root,
                artifact_dir=artifact_root / "docker-remove",
                name="docker-remove-materializer",
                timeout_seconds=120,
            )
            if staging_parent.exists():
                shutil.rmtree(staging_parent, ignore_errors=True)
        return self._verify_existing(instance, destination, image_id)
