from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import yaml


UPSTREAM_URL = "https://github.com/pallets/itsdangerous.git"
UPSTREAM_COMMIT = "672971d66a2ef9f85151e53283113f33d642dabd"
REPO_ID = "itsdangerous_real"
ENCODING_PATH = Path("src/itsdangerous/encoding.py")
TEST_PATH = Path("tests/test_itsdangerous/test_encoding.py")
HEALTHY_LINE = 'return base64.urlsafe_b64encode(string).rstrip(b"=")'
BUGGY_LINE = "return base64.urlsafe_b64encode(string)"


def run(
    argv: list[str],
    cwd: Path | None = None,
    *,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(shlex.quote(value) for value in argv))
    result = subprocess.run(
        argv,
        cwd=cwd,
        input=input_text,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        raise RuntimeError(f"command failed with {result.returncode}: {' '.join(argv)}")
    return result


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_command(runtime_python: Path) -> str:
    return (
        f"env PYTHONPATH=src {shlex.quote(str(runtime_python))} -m pytest -q "
        f"{TEST_PATH.as_posix()}"
    )


def test_env(repo: Path) -> dict[str, str]:
    return {**os.environ, "PYTHONPATH": str(repo / "src")}


def resolve_test_python(source_root: Path) -> Path:
    candidates = (source_root / ".venv/bin/python", Path(sys.executable))
    for candidate in candidates:
        if not candidate.is_file():
            continue
        # Resolving the venv's python symlink loses pyvenv.cfg discovery and
        # silently selects the uv-managed base interpreter instead.
        resolved = candidate.absolute()
        probe = subprocess.run(
            [str(resolved), "-c", "import pytest"],
            text=True,
            capture_output=True,
            check=False,
        )
        if probe.returncode == 0:
            return resolved
    raise RuntimeError("real repository acceptance requires a project Python environment containing pytest")


def prepare_real_repository(root: Path, runtime_python: Path) -> tuple[Path, str]:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    seed = root / "upstream-seed"
    remote = root / "fixture-remote.git"
    main_checkout = root / "main-checkout"

    run(["git", "clone", "--no-checkout", UPSTREAM_URL, str(seed)])
    run(["git", "-C", str(seed), "checkout", "-B", "main", UPSTREAM_COMMIT])
    observed = run(["git", "-C", str(seed), "rev-parse", "HEAD"]).stdout.strip()
    if observed != UPSTREAM_COMMIT:
        raise RuntimeError(f"upstream commit mismatch: expected {UPSTREAM_COMMIT}, got {observed}")
    run(["git", "-C", str(seed), "config", "user.email", "acceptance@example.invalid"])
    run(["git", "-C", str(seed), "config", "user.name", "Autobugfix Acceptance"])

    encoding = seed / ENCODING_PATH
    content = encoding.read_text(encoding="utf-8")
    if content.count(HEALTHY_LINE) != 1:
        raise RuntimeError("pinned upstream encoding contract changed")
    encoding.write_text(content.replace(HEALTHY_LINE, BUGGY_LINE), encoding="utf-8")
    test_file = seed / TEST_PATH
    with test_file.open("a", encoding="utf-8") as handle:
        handle.write(
            "\n\ndef test_base64_encode_omits_url_padding():\n"
            "    assert base64_encode(b\"a\") == b\"YQ\"\n"
        )

    failing = subprocess.run(
        [str(runtime_python), "-m", "pytest", "-q", TEST_PATH.as_posix()],
        cwd=seed,
        env=test_env(seed),
        text=True,
        capture_output=True,
        check=False,
    )
    if failing.returncode == 0:
        raise RuntimeError("fault-injected real repository unexpectedly passes before Autobugfix")
    if "test_base64_encode_omits_url_padding" not in failing.stdout:
        raise RuntimeError(f"fault injection failed for an unexpected reason:\n{failing.stdout}\n{failing.stderr}")

    run(["git", "-C", str(seed), "add", ENCODING_PATH.as_posix(), TEST_PATH.as_posix()])
    run(["git", "-C", str(seed), "commit", "-m", "Inject URL-safe base64 padding regression"])
    fixture_base = run(["git", "-C", str(seed), "rev-parse", "HEAD"]).stdout.strip()
    run(["git", "init", "--bare", str(remote)])
    run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"])
    run(["git", "-C", str(seed), "remote", "rename", "origin", "upstream"])
    run(["git", "-C", str(seed), "remote", "add", "origin", str(remote)])
    run(["git", "-C", str(seed), "push", "-u", "origin", "main"])
    run(["git", "clone", str(remote), str(main_checkout)])
    run(["git", "-C", str(main_checkout), "config", "user.email", "acceptance@example.invalid"])
    run(["git", "-C", str(main_checkout), "config", "user.name", "Autobugfix Acceptance"])
    return main_checkout, fixture_base


def write_control_config(
    control_root: Path,
    main_checkout: Path,
    runtime_python: Path,
    model: str,
) -> None:
    command = test_command(runtime_python)
    config = {
        "task_root": ".autobugfix/tasks",
        "scheduler": {
            "default_max_concurrent": 1,
            "lock_timeout_seconds": 7200,
            "max_auto_iterations": 2,
            "codex_timeout_seconds": 600,
            "writer_timeout_seconds": 600,
            "evaluator_timeout_seconds": 300,
        },
        "codex": {
            "default_model": model,
            "default_timeout_seconds": 600,
            "role_runtime": {
                "enabled": True,
                "runtime_root": ".autobugfix/runtime/codex-sdk",
                "codex_bin": shutil.which("codex"),
                "bridge_auth": True,
                "skill_guard": True,
                "strict_skill_guard": True,
            },
        },
        "repos": {
            REPO_ID: {
                "main_checkout": str(main_checkout),
                "remote": "origin",
                "main_branch": "main",
                "branch_template": "fix/{date}_acceptance_{slug}",
                "test_commands": {"targeted": command, "full": command},
                "ppe": {"enabled": False, "command_template": None},
            }
        },
    }
    write(control_root / ".autobugfix/config.yaml", yaml.safe_dump(config, sort_keys=False))


def autobugfix_cmd(source_root: Path, *args: str) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        str(source_root),
        "--cache-dir",
        "/tmp/uv-cache",
        "autobugfix",
        *args,
    ]


def assert_execution_evidence(task_dir: Path) -> None:
    required = (
        "events.jsonl",
        "logs/writer-1.raw.jsonl",
        "logs/evaluator-1.raw.jsonl",
        "runs/writer-1.md",
        "runs/evaluator-1.md",
        "artifacts/test-result.md",
        "artifacts/diff.patch",
        "artifacts/ppe-brief.md",
    )
    missing = [relative for relative in required if not (task_dir / relative).is_file()]
    empty = [relative for relative in required if (task_dir / relative).is_file() and not (task_dir / relative).stat().st_size]
    if missing or empty:
        raise RuntimeError(f"execution evidence incomplete: missing={missing}, empty={empty}")


def build_eval_case(
    source_root: Path,
    control_root: Path,
    root: Path,
    worktree: Path,
    task_id: str,
    issue: str,
    runtime_python: Path,
) -> tuple[Path, dict[str, object]]:
    run(["git", "-C", str(worktree), "config", "user.email", "acceptance@example.invalid"])
    run(["git", "-C", str(worktree), "config", "user.name", "Autobugfix Acceptance"])
    run(["git", "-C", str(worktree), "add", ENCODING_PATH.as_posix()])
    run(["git", "-C", str(worktree), "commit", "-m", "Restore URL-safe base64 padding contract"])

    raw_path = root / "raw_commit_pairs.jsonl"
    run(
        autobugfix_cmd(
            source_root,
            "dataset",
            "build-raw",
            "--repo",
            REPO_ID,
            "--base-ref",
            "origin/main",
            "--out",
            str(raw_path),
        ),
        cwd=control_root,
    )
    rows = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    matching = [row for row in rows if row.get("raw_id") == task_id]
    if len(matching) != 1:
        raise RuntimeError(f"expected one raw dataset row for {task_id}, got {len(matching)}")
    case = matching[0]
    case.update(
        {
            "task_kind": "bugfix",
            "problem_statement": issue,
            "agent_prompt": issue,
            "expected_behavior": "base64_encode(b'a') returns b'YQ' and the configured pytest command passes",
            "change_summary": "restore unpadded URL-safe base64 output",
            "evidence": "pinned upstream source plus failing regression test",
            "confidence": 1.0,
        }
    )
    dataset_path = root / "real_problem_prompts.jsonl"
    write(dataset_path, json.dumps(case, sort_keys=True) + "\n")

    run_id = "itsdangerous-real-e2e"
    eval_root = root / "eval-runs"
    run(
        autobugfix_cmd(
            source_root,
            "eval",
            "run",
            "--dataset",
            str(dataset_path),
            "--case",
            str(case["raw_id"]),
            "--out",
            str(eval_root),
            "--run-id",
            run_id,
            "--model-mode",
            "codex",
            "--test-command",
            test_command(runtime_python),
            "--codex-timeout-seconds",
            "600",
            "--writer-timeout-seconds",
            "600",
            "--evaluator-timeout-seconds",
            "300",
        ),
        cwd=control_root,
    )
    case_dir = eval_root / run_id / str(case["raw_id"])
    report = yaml.safe_load((case_dir / "report.yaml").read_text(encoding="utf-8"))
    summary = yaml.safe_load((eval_root / run_id / "summary.yaml").read_text(encoding="utf-8"))
    if report.get("decision") != "pass" or report.get("generated_equals_oracle") is not True:
        raise RuntimeError(f"real repository Eval did not match the oracle: {report}")
    if summary.get("failures"):
        raise RuntimeError(f"real repository Eval summary has failures: {summary}")
    if report.get("execution_state") != "waiting_human_ppe_approval":
        raise RuntimeError(f"Eval execution did not stop at the human gate: {report}")
    generated = (case_dir / "generated.diff").read_text(encoding="utf-8")
    oracle = (case_dir / "oracle.diff").read_text(encoding="utf-8")
    if not generated or generated != oracle:
        raise RuntimeError("Eval generated diff is empty or differs from the committed oracle")
    eval_task_dir = case_dir / "control/.autobugfix/tasks" / str(report["task_id"])
    assert_execution_evidence(eval_task_dir)
    if (case_dir / "control/.autobugfix/archive").exists():
        raise RuntimeError("Eval must not archive or accept its Execution task")
    return case_dir, report


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Autobugfix against a pinned real open-source repository with a reproducible injected bug."
    )
    parser.add_argument("--root", default="/tmp/autobugfix-real-repository-e2e")
    parser.add_argument("--model", default="gpt-5.4-mini")
    args = parser.parse_args()

    source_root = Path.cwd().resolve()
    root = Path(args.root).resolve()
    runtime_python = resolve_test_python(source_root)
    main_checkout, fixture_base = prepare_real_repository(root, runtime_python)
    original_main_digest = digest(main_checkout / ENCODING_PATH)
    control_root = root / "control"
    control_root.mkdir()
    shutil.copytree(source_root / ".agents/role-skills", control_root / ".agents/role-skills")
    write_control_config(control_root, main_checkout, runtime_python, args.model)

    run(autobugfix_cmd(source_root, "doctor"), cwd=control_root)
    issue = (
        "ItsDangerous URL-safe base64 encoding has regressed at the configured repository revision. "
        "base64_encode(b'a') now returns padded b'YQ==' although the URL-safe serialization contract "
        "requires b'YQ'. Fix production code only; do not modify tests. Run the configured real pytest command."
    )
    created = run(
        autobugfix_cmd(
            source_root,
            "create",
            "--repo",
            REPO_ID,
            "--title",
            "restore URL-safe base64 padding contract",
            "--from-stdin",
        ),
        cwd=control_root,
        input_text=issue,
    )
    task_id = created.stdout.strip().splitlines()[-1]
    run(autobugfix_cmd(source_root, "run", task_id), cwd=control_root)

    task_dir = control_root / ".autobugfix/tasks" / task_id
    task = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    if task.get("state") != "waiting_human_ppe_approval":
        raise RuntimeError(f"real repository task did not reach the human gate: {task}")
    worktree = Path(str(task["worktree_path"]))
    changed = run(["git", "-C", str(worktree), "diff", "--name-only", "origin/main", "--"]).stdout.splitlines()
    if changed != [ENCODING_PATH.as_posix()]:
        raise RuntimeError(f"Writer changed paths outside production fix: {changed}")
    patch = run(["git", "-C", str(worktree), "diff", "origin/main", "--", ENCODING_PATH.as_posix()]).stdout
    if HEALTHY_LINE not in patch:
        raise RuntimeError(f"Writer did not restore the upstream encoding contract:\n{patch}")
    run(
        [str(runtime_python), "-m", "pytest", "-q", TEST_PATH.as_posix()],
        cwd=worktree,
        env=test_env(worktree),
    )
    assert_execution_evidence(task_dir)

    if digest(main_checkout / ENCODING_PATH) != original_main_digest:
        raise RuntimeError("target main checkout was modified")
    if run(["git", "-C", str(main_checkout), "rev-parse", "HEAD"]).stdout.strip() != fixture_base:
        raise RuntimeError("target main checkout HEAD changed")
    if run(["git", "-C", str(main_checkout), "status", "--porcelain"]).stdout.strip():
        raise RuntimeError("target main checkout is dirty")

    run(autobugfix_cmd(source_root, "gate", task_id, "accepted"), cwd=control_root)
    archive_path = run(
        autobugfix_cmd(source_root, "archive", task_id, "--result", "accepted"),
        cwd=control_root,
    ).stdout.strip().splitlines()[-1]
    archived = Path(archive_path)

    run(autobugfix_cmd(source_root, "memory", "init"), cwd=control_root)
    run(autobugfix_cmd(source_root, "memory", "collect", task_id), cwd=control_root)
    run(autobugfix_cmd(source_root, "memory", "digest", task_id), cwd=control_root)
    run(autobugfix_cmd(source_root, "memory", "lint"), cwd=control_root)
    proposal_dir = Path(
        run(autobugfix_cmd(source_root, "memory", "maintain", task_id), cwd=control_root)
        .stdout.strip()
        .splitlines()[-1]
    )
    proposal = yaml.safe_load((proposal_dir / "proposal.yaml").read_text(encoding="utf-8"))
    if proposal.get("status") != "pending":
        raise RuntimeError(f"Memory proposal self-approved or has an invalid status: {proposal}")
    archived_task = yaml.safe_load((archived / "task.yaml").read_text(encoding="utf-8"))
    if archived_task.get("state") != "archived" or archived_task.get("archived_result") != "accepted":
        raise RuntimeError(f"Execution archive identity changed during Memory processing: {archived_task}")

    eval_case_dir, eval_report = build_eval_case(
        source_root,
        control_root,
        root,
        worktree,
        task_id,
        issue,
        runtime_python,
    )
    print(
        json.dumps(
            {
                "task_id": task_id,
                "model": args.model,
                "upstream": UPSTREAM_URL,
                "upstream_commit": UPSTREAM_COMMIT,
                "fixture_base": fixture_base,
                "generated_paths": changed,
                "archive": str(archived),
                "memory_proposal": str(proposal_dir),
                "memory_status": proposal["status"],
                "eval_case_dir": str(eval_case_dir),
                "eval_decision": eval_report["decision"],
                "eval_generated_equals_oracle": eval_report["generated_equals_oracle"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
