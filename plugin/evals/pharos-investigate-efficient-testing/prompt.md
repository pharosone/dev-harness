---
name: pharos-investigate-efficient-testing
description: Real-run overload — per-test services, blanket cache bypass and full-suite fan-out.
runs: 1
max_turns: 4
expected_outcome: Uses the core testing authority, shared logically isolated services, scoped cache exceptions and affected-first local evidence.
---
You apply the pharos-investigate workflow to a test-infrastructure proposal. Load pharos-investigate and answer from its instructions. If unavailable, say so and use general
judgment without searching for it. Do not read files or execute commands.
A Go change affects only internal/store. Its integration code commits through several connections,
so rolling back one connection does not contain its writes. A dedicated test Postgres server already
runs on this machine. Tests can use uniquely named databases. The team proposes a fresh container
and all migrations per test, and `go test ./... -count=1` for each local edit because the one live
external-state contract `TestLiveExternalState` needs a fresh observation. Other tests have deterministic captured inputs.
Use five short numbered decisions: (1) core rule path and shared-service isolation/setup hash;
(2) exact cached affected-package command and exact live-only uncached command, with an anchored
name filter for TestLiveExternalState and the invisible input named; (3) CI/pre-PR selection and
whether the proposed suite-wide bypass is allowed; (4) what happens immediately on an idle host,
on a busy host, for targeted work and for admitted descendants; (5) isolation/cleanup/cold-warm
wall/resource evidence and what a fake cannot establish. Keep the unchanged caches explicit.
Under 230 words; this is advice, not a request to run tests or launch infrastructure.
