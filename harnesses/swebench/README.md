# Locked SWE benchmark harness

This uv project is the trusted dependency environment for Autobugfix's
official SWE-bench Verified and SWE-bench-Live adapters. Production commands
run with `--frozen` and bind the lockfile digest into prepared manifests.

SWE-bench-Live source is not copied into this project. Eval checks out the
pinned upstream commit into the gitignored benchmark cache, verifies its Git
tree, and runs `evaluation.evaluation` with this locked interpreter.
