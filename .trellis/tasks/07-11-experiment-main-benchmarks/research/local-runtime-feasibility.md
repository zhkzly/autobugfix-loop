# Local runtime feasibility

## Host audit on 2026-07-11

- Architecture: `x86_64`.
- CPU: 12 logical CPUs.
- Memory: 15 GiB visible in WSL, 12 GiB available during the audit.
- Swap: 4 GiB.
- Workspace disk: approximately 950 GiB available.
- `/tmp`: approximately 7.5 GiB available.
- Git: 2.53.0.
- Perl: 5.40.1.
- uv: 0.11.24.
- Java: missing.
- Subversion: missing.
- `cpanm`: missing.
- Docker client/daemon: unavailable in the WSL distribution; Docker Desktop
  reported that WSL integration was not enabled.

All official benchmark Git repositories were reachable with `git ls-remote`.

## Consequences

- Defects4J is structurally compatible but cannot yet pass a local real-case
  acceptance because Java 11, Subversion, and `cpanm` are absent.
- SWE-bench and SWE-bench-Live cannot run their official container harnesses
  until Docker Engine or Docker Desktop WSL integration is available.
- Available CPU and disk satisfy the published SWE-bench recommendations.
  Reported memory is just below the stated 16 GiB recommendation, so execution
  must start with one worker and retain resource telemetry.
- `/tmp` is too small for benchmark images and repository caches. Configurable
  durable cache roots under the workspace or another large filesystem are
  required.

## Required no-model acceptance before SDK calls

1. Run `autobugfix doctor` plus benchmark-specific doctor checks.
2. Install/enable the exact required external runtimes.
3. Pin framework and dataset revisions.
4. Reproduce each selected buggy revision's failure.
5. Apply or checkout the official gold revision and reproduce success.
6. Repeat flaky-sensitive checks and reject unstable cases before sealing the
   manifest.

No production model call may be used to compensate for a missing or broken
benchmark runtime.
