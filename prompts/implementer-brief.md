# <unit title>

## Goal
<observable outcome; one sentence>

## Contract
<exact behavior, acceptance cases and invariants>

## Scope
Checkout: <absolute worktree>
Base: <commit/ref>
Allowed paths: <files/globs>
Rules: <project path rules> and harness/core/rules/efficient-testing.md
Run directory: <run>
Profile/model/effort: <resolved harness profile and tuple>
Budget: <=80 responses

## Test infrastructure
Isolation level: <in-process fake / shared logical service / per-run / per-test>
Why this level is correct and cheaper levels are insufficient: <boundary, or no new infrastructure>
Owned namespace and cleanup: <project/run/worker/test identity; cleanup after failure>
Setup reuse and content hash: <migrations/fixtures/assets/model inputs, or none>
Caches: <kept on; any exception names an invisible input and the one affected test/step>
Affected local commands: <narrowest cases/packages and real boundary checks>
Parallelism and cost: <host budget; cold/warm wall time, starts, CPU/memory or unknown>
Full suite: <CI/pre-PR gate; earlier expansion needs a concrete reason>

## Evidence and report
Tests first; save the real red output, freeze, implement, verify and record first green.
Progress: <report>.progress.md
Final report: <report>, written only when work ends; reply only its path.
Questions: <report>.flag.md when the coordinator requires that channel; otherwise the run's
watched lane/tripwire channel. Never type into another pane. No push/tag/PR without authorization.
