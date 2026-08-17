from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Mapping

import yaml

from autobugfix.eval.benchmarks.models import (
    digest_file,
    record_with_digest,
    safe_component,
)
from autobugfix.eval.benchmarks.runtime import run_command
from autobugfix.eval.benchmarks.swe_models import (
    SWEInstance,
    SWEOfficialResult,
    SWESubmission,
)
from autobugfix.eval.benchmarks.swe_runtime import SWERuntime, SWERuntimeError
from autobugfix.models import utc_now


class SWEOfficialRunner:
    def __init__(self, runtime: SWERuntime, adapter: str):
        if adapter not in SWERuntime.ADAPTERS:
            raise SWERuntimeError(f"unsupported SWE adapter: {adapter}")
        self.runtime = runtime
        self.adapter = adapter
        self.snapshot = runtime.read_dataset_snapshot(adapter)

    def _row(self, instance_id: str) -> dict[str, Any]:
        safe_component(instance_id, "instance_id")
        with Path(self.snapshot.path).open("r", encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                if row.get("instance_id") == instance_id:
                    if not isinstance(row, dict):
                        break
                    return row
        raise SWERuntimeError(f"SWE instance not found: {instance_id}")

    @staticmethod
    def _resolved_bool(value: Any) -> bool:
        if type(value) is not bool:
            raise ValueError("official resolved value is not boolean")
        return value

    @staticmethod
    def _verified_submission_failure(log_path: Path) -> bool:
        if not log_path.is_file():
            return False
        log = log_path.read_text(encoding="utf-8", errors="replace")
        return any(
            marker in log
            for marker in (
                ">>>>> Patch Apply Failed",
                "Failed to apply patch to container",
                "Test timed out after",
            )
        )

    def load_instance(self, instance_id: str, artifact_root: Path) -> SWEInstance:
        row = self._row(instance_id)
        if self.adapter == "swebench_live":
            return SWEInstance.from_live(row)
        uv = shutil.which("uv")
        if not uv:
            raise SWERuntimeError("uv executable is unavailable")
        evidence = run_command(
            [
                uv,
                "run",
                "--project",
                str(self.runtime.config.harness_project),
                "--frozen",
                "python",
                str(
                    self.runtime.config.harness_project
                    / "scripts/inspect_verified.py"
                ),
                "--dataset",
                self.snapshot.path,
                "--instance-id",
                instance_id,
                "--namespace",
                self.runtime.config.verified_namespace or "none",
            ],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "inspect-instance",
            name="inspect-verified-instance",
            timeout_seconds=self.runtime.benchmark_config.command_timeout_seconds,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        if not evidence.passed:
            raise SWERuntimeError("official SWE-bench instance inspection failed")
        try:
            image = json.loads(Path(evidence.stdout_path).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SWERuntimeError("official SWE-bench instance inspection was invalid") from exc
        if not isinstance(image, Mapping):
            raise SWERuntimeError("official SWE-bench instance inspection was not a mapping")
        return SWEInstance.from_verified(row, image)

    def prepare_verified_image(
        self,
        instance: SWEInstance,
        artifact_root: Path,
    ) -> dict[str, Any] | None:
        """Import one selected official image by digest into the local tag."""

        if self.adapter != "swebench_verified":
            return None
        pin = self.runtime.verified_image_pin(instance.instance_id)
        if pin is None:
            return None
        docker = shutil.which("docker")
        if not docker:
            raise SWERuntimeError("docker executable is unavailable")
        artifact_root.mkdir(parents=True, exist_ok=False)
        pull = run_command(
            [
                docker,
                "pull",
                "--platform",
                self.runtime.config.platform,
                pin["source_ref"],
            ],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-pull-pinned",
            name="docker-pull-pinned-instance",
            timeout_seconds=self.runtime.benchmark_config.command_timeout_seconds,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        if not pull.passed:
            raise SWERuntimeError(
                f"failed to pull pinned official image: {instance.instance_id}"
            )
        source_inspect = run_command(
            [docker, "image", "inspect", "--format", "{{json .}}", pin["source_ref"]],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-inspect-pinned",
            name="docker-inspect-pinned-instance",
            timeout_seconds=60,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        source = self._image_inspection(source_inspect, "pinned official image")
        manifest_inspect = run_command(
            [docker, "manifest", "inspect", pin["source_ref"]],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-inspect-manifest",
            name="docker-inspect-pinned-manifest",
            timeout_seconds=60,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        manifest = self._image_inspection(
            manifest_inspect, "pinned OCI manifest"
        )
        descriptor = source.get("Descriptor")
        if (
            not isinstance(descriptor, Mapping)
            or descriptor.get("digest")
            != f"sha256:{pin['manifest_digest']}"
            or source.get("Os") != "linux"
            or source.get("Architecture") != "amd64"
        ):
            raise SWERuntimeError(
                "pinned official image manifest or platform differs from authority"
            )
        tag = run_command(
            [docker, "image", "tag", pin["source_ref"], instance.docker_image],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-tag-local",
            name="docker-tag-pinned-instance",
            timeout_seconds=60,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        if not tag.passed:
            raise SWERuntimeError("failed to bind pinned image to local scorer tag")
        local_inspect = run_command(
            [
                docker,
                "image",
                "inspect",
                "--format",
                "{{json .}}",
                instance.docker_image,
            ],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-inspect-local",
            name="docker-inspect-tagged-instance",
            timeout_seconds=60,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        local = self._image_inspection(local_inspect, "tagged local image")
        image_id = str(source.get("Id") or "")
        root_fs = source.get("RootFS")
        raw_diff_ids = (
            root_fs.get("Layers") if isinstance(root_fs, Mapping) else None
        )
        manifest_config = manifest.get("config")
        manifest_layers = manifest.get("layers")
        if (
            not image_id.startswith("sha256:")
            or local.get("Id") != image_id
            or local.get("RootFS") != root_fs
            or not isinstance(raw_diff_ids, list)
            or not raw_diff_ids
            or not all(
                isinstance(layer, str) and layer.startswith("sha256:")
                for layer in raw_diff_ids
            )
            or not isinstance(manifest_config, Mapping)
            or not isinstance(manifest_layers, list)
            or not manifest_layers
            or not all(isinstance(layer, Mapping) for layer in manifest_layers)
        ):
            raise SWERuntimeError(
                "tagged local image differs from pinned official image"
            )
        receipt = record_with_digest(
            {
                "schema": "autobugfix-swe-pinned-image-import-v1",
                "instance_id": instance.instance_id,
                "source_ref": pin["source_ref"],
                "manifest_digest": pin["manifest_digest"],
                "manifest_record_digest": (
                    self.runtime.verified_image_manifest_digest
                ),
                "local_image": instance.docker_image,
                "local_image_id": image_id,
                "config_digest": self._sha256_digest(
                    manifest_config.get("digest"), "manifest config"
                ),
                "layer_digests": [
                    self._sha256_digest(layer.get("digest"), "manifest layer")
                    for layer in manifest_layers
                ],
                "rootfs_diff_ids": [
                    self._sha256_digest(diff_id, "rootfs diff ID")
                    for diff_id in raw_diff_ids
                ],
                "platform": self.runtime.config.platform,
                "pull_command_digest": pull.to_dict()["record_digest"],
                "source_inspect_command_digest": (
                    source_inspect.to_dict()["record_digest"]
                ),
                "manifest_inspect_command_digest": (
                    manifest_inspect.to_dict()["record_digest"]
                ),
                "tag_command_digest": tag.to_dict()["record_digest"],
                "local_inspect_command_digest": (
                    local_inspect.to_dict()["record_digest"]
                ),
            }
        )
        receipt_path = artifact_root / "receipt.yaml"
        receipt_path.write_text(
            yaml.safe_dump(receipt, sort_keys=False), encoding="utf-8"
        )
        return {**receipt, "receipt_path": str(receipt_path.resolve())}

    @staticmethod
    def _sha256_digest(value: object, label: str) -> str:
        digest = str(value or "").removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise SWERuntimeError(f"{label} digest is invalid")
        return digest

    @staticmethod
    def _image_inspection(command: Any, label: str) -> Mapping[str, Any]:
        if not command.passed:
            raise SWERuntimeError(f"{label} inspection failed")
        try:
            raw = json.loads(
                Path(command.stdout_path).read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, UnicodeError) as exc:
            raise SWERuntimeError(f"{label} inspection is invalid") from exc
        if not isinstance(raw, Mapping):
            raise SWERuntimeError(f"{label} inspection is not a mapping")
        return raw

    def image_id(
        self,
        instance: SWEInstance,
        artifact_root: Path,
        *,
        allow_pull: bool = True,
    ) -> str:
        docker = shutil.which("docker")
        if not docker:
            raise SWERuntimeError("docker executable is unavailable")
        local = run_command(
            [docker, "image", "inspect", "--format", "{{.Id}}", instance.docker_image],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-inspect-local",
            name="docker-inspect-local-instance",
            timeout_seconds=60,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        if local.passed:
            image_id = Path(local.stdout_path).read_text(encoding="utf-8").strip()
            if image_id.startswith("sha256:"):
                return image_id
        if not allow_pull:
            raise SWERuntimeError(
                f"official image is not materialized: {instance.docker_image}"
            )
        if (
            self.adapter == "swebench_verified"
            and self.runtime.config.verified_namespace is None
        ):
            raise SWERuntimeError(
                "local-build SWE-bench image must be created by the official scorer"
            )
        pull = run_command(
            [docker, "pull", "--platform", self.runtime.config.platform, instance.docker_image],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-pull",
            name="docker-pull-instance",
            timeout_seconds=self.runtime.benchmark_config.command_timeout_seconds,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        if not pull.passed:
            raise SWERuntimeError(f"failed to pull official image: {instance.docker_image}")
        inspect = run_command(
            [docker, "image", "inspect", "--format", "{{.Id}}", instance.docker_image],
            cwd=self.runtime.project_root,
            artifact_dir=artifact_root / "docker-inspect",
            name="docker-inspect-instance",
            timeout_seconds=60,
            env=self.runtime.command_env(),
            inherit_env=False,
        )
        image_id = Path(inspect.stdout_path).read_text(encoding="utf-8").strip()
        if not inspect.passed or not image_id.startswith("sha256:"):
            raise SWERuntimeError("official image has no immutable image ID")
        return image_id

    def _verified_command(
        self,
        instance: SWEInstance,
        prediction: str,
        run_id: str,
        official_root: Path,
    ) -> list[str]:
        uv = shutil.which("uv")
        if not uv:
            raise SWERuntimeError("uv executable is unavailable")
        return [
            uv,
            "run",
            "--project",
            str(self.runtime.config.harness_project),
            "--frozen",
            "python",
            str(self.runtime.config.harness_project / "scripts/run_official.py"),
            "--build-network-mode",
            self.runtime.config.verified_build_network_mode,
            "--module",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            self.snapshot.path,
            "--predictions_path",
            prediction,
            "--instance_ids",
            instance.instance_id,
            "--max_workers",
            "1",
            "--run_id",
            run_id,
            "--namespace",
            self.runtime.config.verified_namespace or "none",
            "--cache_level",
            "instance",
            "--clean",
            "false",
            "--timeout",
            str(self.runtime.config.scorer_timeout_seconds),
            "--report_dir",
            str(official_root),
        ]

    def _live_command(
        self,
        instance: SWEInstance,
        prediction: str,
        official_root: Path,
    ) -> list[str]:
        uv = shutil.which("uv")
        if not uv:
            raise SWERuntimeError("uv executable is unavailable")
        return [
            uv,
            "run",
            "--project",
            str(self.runtime.config.harness_project),
            "--frozen",
            "python",
            str(self.runtime.live_checkout / "evaluation/evaluation.py"),
            "--dataset",
            self.snapshot.path,
            "--instance_ids",
            instance.instance_id,
            "--platform",
            "linux",
            "--patch_dir",
            prediction,
            "--output_dir",
            str(official_root),
            "--workers",
            "1",
            "--overwrite",
            "1",
        ]

    def score(
        self,
        instance: SWEInstance,
        artifact_root: Path,
        *,
        run_id: str,
        submission: SWESubmission | None = None,
        gold: bool = False,
        null: bool = False,
        expected_image_id: str | None = None,
    ) -> SWEOfficialResult:
        safe_component(run_id, "run_id")
        if sum((gold, submission is not None, null)) != 1:
            raise SWERuntimeError(
                "official scoring requires exactly one of gold, null, or submission"
            )
        artifact_root.mkdir(parents=True, exist_ok=False)
        official_root = artifact_root / "official"
        official_root.mkdir()
        if (
            self.adapter == "swebench_verified"
            and expected_image_id is None
            and self.runtime.verified_image_pin(instance.instance_id) is not None
        ):
            imported = self.prepare_verified_image(
                instance, artifact_root / "image-source"
            )
            assert imported is not None
            expected_image_id = str(imported["local_image_id"])
        prediction: str
        prediction_path: Path | None = None
        model_name = "gold"
        if gold:
            prediction = "gold"
        else:
            if null:
                patch = ""
            else:
                assert submission is not None
                patch = submission.patch
            prediction_path = artifact_root / (
                "prediction.jsonl" if self.adapter == "swebench_verified" else "prediction.json"
            )
            model_name = "autobugfix-null" if null else "autobugfix-subject"
            if self.adapter == "swebench_verified":
                prediction_path.write_text(
                    json.dumps(
                        {
                            "instance_id": instance.instance_id,
                            "model_name_or_path": model_name,
                            "model_patch": patch,
                        },
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
            else:
                prediction_path.write_text(
                    json.dumps(
                        {
                            instance.instance_id: {
                                "model_patch": patch,
                            }
                        },
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
            prediction = str(prediction_path)

        image_id = "pending"
        if expected_image_id is not None:
            if not expected_image_id.startswith("sha256:"):
                raise SWERuntimeError("expected SWE image ID is not immutable")
            image_id = self.image_id(
                instance,
                artifact_root / "image",
                allow_pull=False,
            )
            if image_id != expected_image_id:
                raise SWERuntimeError("official scorer image differs from generation image")
        elif not (
            self.adapter == "swebench_verified"
            and self.runtime.config.verified_namespace is None
        ):
            image_id = self.image_id(instance, artifact_root / "image")
        started_at = utc_now()
        client_state = artifact_root / "scorer-client-state"
        client_state.mkdir(mode=0o700)
        if self.adapter == "swebench_verified":
            argv = self._verified_command(instance, prediction, run_id, official_root)
            cwd = official_root
            command_env = self.runtime.command_env(client_state)
        else:
            self.runtime.verify_live_checkout()
            argv = self._live_command(instance, prediction, official_root)
            cwd = official_root
            command_env = self.runtime.live_command_env(client_state)
        argv = self.runtime.isolated_official_argv(
            argv,
            cwd=cwd,
            writable_roots=(official_root, client_state),
            readable_roots=(prediction_path,) if prediction_path is not None else (),
            # Upstream SWE-bench resolves public requirements at pinned commits
            # while constructing some test specifications.  This is the only
            # caller that retains network access, and it runs after submission
            # freeze in the Eval authority plane.
            allow_network=True,
        )
        command = run_command(
            argv,
            cwd=cwd,
            artifact_dir=artifact_root / "command",
            name="official-swe-score",
            timeout_seconds=self.runtime.config.scorer_timeout_seconds + 300,
            env=command_env,
            inherit_env=False,
        )
        image_id_before_score = image_id
        if image_id == "pending":
            try:
                image_id = self.image_id(
                    instance,
                    artifact_root / "image-after-score",
                    allow_pull=False,
                )
            except SWERuntimeError:
                image_id = "unavailable"
        else:
            try:
                image_id_after_score = self.image_id(
                    instance,
                    artifact_root / "image-after-score",
                    allow_pull=False,
                )
            except SWERuntimeError:
                image_id_after_score = "unavailable"

        if self.adapter == "swebench_verified":
            report_path = (
                official_root
                / "logs/run_evaluation"
                / run_id
                / model_name
                / instance.instance_id
                / "report.json"
            )
            aggregate_path = official_root / f"{model_name}.{run_id}.json"
            harness_error = ""
            resolved = False
            if not command.passed:
                harness_error = "official SWE-bench command failed"
            elif not aggregate_path.is_file():
                harness_error = "official SWE-bench aggregate report is missing"
            else:
                try:
                    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
                    if not isinstance(aggregate, Mapping):
                        raise ValueError("aggregate is not a mapping")
                    error_ids = set(aggregate.get("error_ids") or [])
                    empty_ids = set(aggregate.get("empty_patch_ids") or [])
                    completed_ids = set(aggregate.get("completed_ids") or [])
                    if instance.instance_id in error_ids:
                        log_path = report_path.parent / "run_instance.log"
                        if null or not self._verified_submission_failure(log_path):
                            harness_error = "official SWE-bench reported a harness error"
                        resolved = False
                    elif instance.instance_id in empty_ids:
                        resolved = False
                    elif instance.instance_id not in completed_ids:
                        harness_error = "official SWE-bench did not classify the submission"
                    elif not report_path.is_file():
                        harness_error = "official SWE-bench case report is missing"
                    else:
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                        if not isinstance(report, Mapping):
                            raise ValueError("case report is not a mapping")
                        case_report = report.get(instance.instance_id)
                        if not isinstance(case_report, Mapping):
                            raise ValueError("case result is not a mapping")
                        resolved = self._resolved_bool(case_report.get("resolved"))
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    harness_error = f"official SWE-bench report is invalid: {exc}"
        else:
            report_path = official_root / instance.instance_id / "report.json"
            aggregate_path = official_root / "results.json"
            harness_error = ""
            resolved = False
            if not command.passed:
                harness_error = "official SWE-bench-Live command failed"
            elif not aggregate_path.is_file():
                harness_error = "official SWE-bench-Live aggregate report is missing"
            else:
                try:
                    aggregate = json.loads(aggregate_path.read_text(encoding="utf-8"))
                    if not isinstance(aggregate, Mapping):
                        raise ValueError("aggregate is not a mapping")
                    error_ids = set(aggregate.get("error_ids") or [])
                    empty_ids = set(aggregate.get("empty_patch_ids") or [])
                    completed_ids = set(aggregate.get("success_ids") or []) | set(
                        aggregate.get("failure_ids") or []
                    )
                    if instance.instance_id in error_ids:
                        command_stdout = Path(command.stdout_path)
                        if null or not self._live_submission_failure(command_stdout):
                            harness_error = (
                                "official SWE-bench-Live reported a harness error"
                            )
                        resolved = False
                    elif instance.instance_id in empty_ids:
                        resolved = False
                    elif instance.instance_id not in completed_ids:
                        harness_error = (
                            "official SWE-bench-Live did not classify the submission"
                        )
                    elif not report_path.is_file():
                        harness_error = "official SWE-bench-Live case report is missing"
                    else:
                        report = json.loads(report_path.read_text(encoding="utf-8"))
                        if not isinstance(report, Mapping):
                            raise ValueError("case report is not a mapping")
                        resolved = self._resolved_bool(report.get("resolved"))
                except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                    harness_error = f"official SWE-bench-Live report is invalid: {exc}"

        if image_id == "unavailable":
            harness_error = harness_error or "official SWE image identity is unavailable"
        if (
            image_id_before_score != "pending"
            and image_id_after_score != image_id_before_score
        ):
            harness_error = "official SWE image identity changed during scoring"
            image_id = image_id_after_score
        if expected_image_id is not None and image_id != expected_image_id:
            harness_error = "official scorer image differs from generation image"

        return SWEOfficialResult(
            adapter=instance.adapter,
            instance_id=instance.instance_id,
            run_id=run_id,
            resolved=resolved,
            harness_error=harness_error,
            image=instance.docker_image,
            image_id=image_id,
            command=command.to_dict(),
            report_path=str(report_path.resolve()) if report_path.exists() else "missing",
            report_sha256=digest_file(report_path) if report_path.exists() else "missing",
            output_root=str(official_root.resolve()),
            started_at=started_at,
            finished_at=utc_now(),
        )

    @staticmethod
    def _live_submission_failure(log_path: Path) -> bool:
        if not log_path.is_file():
            return False
        text = log_path.read_text(encoding="utf-8", errors="replace")
        return (
            "PATCH FAILED TO APPLY CLEANLY" in text
            and "Error processing instance" in text
        )
