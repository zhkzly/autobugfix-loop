from __future__ import annotations

import argparse
import json
from pathlib import Path

from swebench.harness.test_spec.test_spec import make_test_spec


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--namespace", default="none")
    args = parser.parse_args()

    instance = None
    with args.dataset.open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("instance_id") == args.instance_id:
                instance = row
                break
    if instance is None:
        raise SystemExit(f"instance not found: {args.instance_id}")
    namespace = None if args.namespace.lower() == "none" else args.namespace
    spec = make_test_spec(instance, namespace=namespace)
    print(
        json.dumps(
            {
                "instance_id": spec.instance_id,
                "repository": spec.repo,
                "base_commit": instance["base_commit"],
                "language": spec.language,
                "docker_image": spec.instance_image_key,
                "platform": spec.platform,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
