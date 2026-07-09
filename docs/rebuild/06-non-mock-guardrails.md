Non-Mock Guardrails
This document exists because AI agents often satisfy an interface by generating mock code that prints plausible output. That is explicitly forbidden for this project.
Production Paths Must Be Real
These production paths must perform real work:
autobugfix create
reads real config;
validates the real target repo;
writes a real task directory;
runs real Git sync/worktree creation.
autobugfix run
uses the real Codex Python SDK backend unless an explicit test-only fake is
injected;
launches writer in the real task worktree;
lets writer edit real files;
runs the configured verifier command;
launches evaluator;
writes real artifacts/logs/events.
autobugfix worker
starts/stops/observes a real background process or daemon loop;
writes heartbeat and log files.
autobugfix memory maintain
uses the real memory maintainer backend in production;
writes proposal outputs through deterministic validation/copying.
autobugfix eval run
clones or prepares a real isolated repo/control root;
calls the real execution loop unless --skip-execution is explicitly set;
writes real generated/oracle diffs and scores.
Where Fakes Are Allowed
Fakes are allowed only in tests and must be opt-in:
fake Codex backend for deterministic unit tests;
fake evaluator decision sequence for runner unit tests;
fake model judge for eval scorer unit tests or cost-controlled scoring.
Fakes must not be the default production behavior.
Required Evidence Files
A real run must leave evidence:
.autobugfix/tasks/<task-id>/events.jsonl
.autobugfix/tasks/<task-id>/runs/*.md
.autobugfix/tasks/<task-id>/artifacts/diff.patch
.autobugfix/tasks/<task-id>/artifacts/test-result.md
.autobugfix/tasks/<task-id>/logs/*.raw.jsonl
.autobugfix/tasks/<task-id>/logs/*.stderr.log

If these files are missing after a claimed successful run, the implementation is not acceptable.
Explicit Anti-Patterns
Reject code that:
returns hardcoded task IDs;
creates directories without using Git worktrees;
skips git fetch/pull/merge safety for configured main checkouts;
edits target repo main checkout directly;
writes diff.patch without running Git diff;
writes test-result.md without running the verifier command;
marks evaluator pass without invoking evaluator logic;
creates memory proposals without traceability to raw/digest evidence;
treats eval scorer output as the execution result;
hides errors by writing a success summary;
changes task state from Gradio or controller without AutobugfixService;
parses task/event files separately in each adapter instead of using
projection helpers.
Mock Detection Checklist
Before accepting generated code, run:
rg -n "TODO|stub|placeholder|mock|fake|not implemented|pass$" src tests
rg -n "return .*created|return .*pass|hardcoded|sample output" src
rg -n "mars_agent|/Users/|bytedance|autobugfix-worktrees" src README.md AGENTS.md .agents

Findings are not automatically fatal; tests may contain fake helpers. But every fake must be test-scoped, named as such, and not used by production CLI paths.
Acceptance Over Unit Tests
Unit tests prove contracts. They do not prove the product is real. The rebuild is not complete until 05-real-acceptance.md passes against a toy repo.