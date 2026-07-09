from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from autobugfix.operator.validator import validate_operator_request


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Autobugfix operator governance policy.")
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--base-ref", default="HEAD")
    parser.add_argument("--run-validation-commands", action="store_true")
    parser.add_argument("--validation-timeout-seconds", type=int)
    parser.add_argument("--no-record", action="store_true")
    args = parser.parse_args()
    report = validate_operator_request(
        Path.cwd(),
        args.request_id,
        base_ref=args.base_ref,
        run_validation_commands=args.run_validation_commands,
        validation_timeout_seconds=args.validation_timeout_seconds,
        record=not args.no_record,
    )
    print(yaml.safe_dump(report, sort_keys=False).strip())
    return 0 if report["policy"]["allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
