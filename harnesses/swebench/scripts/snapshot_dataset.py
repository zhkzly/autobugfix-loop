from __future__ import annotations

import argparse
import json
from pathlib import Path

from datasets import load_dataset


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--split", default="test")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    if args.split == "all":
        collection = load_dataset(args.dataset, revision=args.revision)
        rows = [
            {**dict(row), "autobugfix_dataset_split": split}
            for split in sorted(collection)
            for row in collection[split]
        ]
        rows.sort(key=lambda row: str(row.get("instance_id") or ""))
    else:
        dataset = load_dataset(
            args.dataset,
            split=args.split,
            revision=args.revision,
        )
        rows = [dict(row) for row in dataset]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        for row in rows:
            stream.write(
                json.dumps(row, sort_keys=True, ensure_ascii=True) + "\n"
            )
    print(json.dumps({"dataset": args.dataset, "revision": args.revision, "split": args.split, "rows": len(rows)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
