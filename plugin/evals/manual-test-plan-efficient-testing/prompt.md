---
name: manual-test-plan-efficient-testing
description: Real-run overload — per-test services, blanket cache bypass and full-suite fan-out.
runs: 1
max_turns: 4
expected_outcome: Uses the core testing authority, shared logically isolated services, scoped cache exceptions and affected-first local evidence.
---
You apply the manual-test-plan workflow to a test-infrastructure proposal. Load manual-test-plan if available;
answer from available instructions without searching for missing skills, reading files or executing commands.
A Go change affects only internal/store. Its integration code commits through several connections,
so rolling back one connection does not contain its writes. A dedicated test Postgres server already
runs on this machine. Tests can use uniquely named databases. The team proposes a fresh container
and all migrations per test, and `go test ./... -count=1` for each local edit because one live
external-state contract needs a fresh observation. Other tests have deterministic captured inputs.
Give the core rule path, the cheapest correct service/isolation and reusable migration setup key,
what cache exception is permitted and why, the local vs CI/pre-PR selection, machine admission
behavior and the brief/QA cost evidence required. Explain what a fake cannot establish. Under
230 words; this is advice for your workflow, not a request to run tests or launch infrastructure.
