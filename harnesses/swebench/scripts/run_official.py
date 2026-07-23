from __future__ import annotations

import argparse
import runpy
import sys
from collections.abc import Sequence
from typing import Any


BUILD_NETWORK_MODES = frozenset({"default", "host"})
OFFICIAL_MODULE = "swebench.harness.run_evaluation"


def install_build_network_mode(api_client: type[Any], mode: str) -> None:
    """Bind SWE-bench image construction to an explicit Docker network mode."""
    if mode not in BUILD_NETWORK_MODES:
        raise ValueError(f"unsupported SWE build network mode: {mode}")
    if mode == "default":
        return

    original_build = api_client.build

    def build_with_host_network(self: Any, *args: Any, **kwargs: Any) -> Any:
        configured = kwargs.get("network_mode")
        if configured not in {None, "host"}:
            raise RuntimeError("official SWE build received a conflicting network mode")
        kwargs = dict(kwargs)
        kwargs["network_mode"] = "host"
        return original_build(self, *args, **kwargs)

    api_client.build = build_with_host_network


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--build-network-mode",
        choices=sorted(BUILD_NETWORK_MODES),
        required=True,
    )
    parser.add_argument("--module", choices=(OFFICIAL_MODULE,), required=True)
    args, upstream_argv = parser.parse_known_args(argv)
    if not upstream_argv:
        parser.error("official SWE-bench arguments are required")

    if args.build_network_mode == "host":
        import docker

        install_build_network_mode(docker.APIClient, args.build_network_mode)

    sys.argv = [args.module, *upstream_argv]
    runpy.run_module(args.module, run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
