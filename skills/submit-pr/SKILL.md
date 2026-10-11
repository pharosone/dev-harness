---
name: submit-pr
description: "Use when the user asks to open, update or finalize a pull request for work in this repository (\"сделай PR\", \"открой PR\", \"submit\", \"push and open a PR\"). Never on its own initiative."
---

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/submit-pr.md` (this skill's project specifics) before following this
procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it. Resolve task artifacts with `python3 harness/core/scripts/task_state.py locate <task>`;
use the returned absolute directory, including for an existing legacy run. `<run-dir>` below
means that resolved directory. Reuse existing user authorization; publish comments or reviews
only when the user explicitly requested it or explicitly invoked a skill authorizing that action.

**Dispatch authority:** resolve every orchestrator/worker model and effort from
`harness/core/adapters/models.json` deep-merged with the active
`harness/project/models.json` (or `$HARNESS_PROJECT/models.json`). Record profile, model
and effort. Never select from personal memory or saved preferences; router shadow is
advisory and cannot replace the actual static profile choice. Lane progress goes to
`<report>.progress.md`; write final `<report>` only at the end. Requests use
`<run>/lanes/<work>.flag.md` or `tripwire.py flag`; never type into another pane.

# Submit PR

Opens or updates a PR on the repository's GitHub remote after the work is verified, with a
description a reviewer can trust and one recorded review round. Commit and push only because the
user asked.

## Efficient testing authority

Apply `harness/core/rules/efficient-testing.md` to this workflow's tests and recommendations.
Choose in-process fakes first, then one shared long-lived test service with owned logical
isolation, then per-run services, then per-test containers only for a written correctness
reason. Reuse expensive setup with complete content-hash keys. Keep build/result/dependency/
image caches enabled; bypass only a named test/step for an input that its cache cannot see.
Run affected cases/packages first locally; the full suite remains required in CI/pre-PR.
Heavy gates/evals/reviews use `harness/core/scripts/load_guard.py` for adaptive load/memory
admission, never fixed slots; targeted runs
and nested admitted work do not wait. Infrastructure changes name the isolation boundary,
cleanup and measured cold/warm wall/resource cost. Never replace required real paired model
measurements or real-service boundary coverage with a fake.

## 1. Pre-flight

- The branch is not the default branch (`main`); commits follow the project's convention
  (conventional by default: `feat(<area>): …`, `fix(<area>): …`, `docs: …`, `chore: …`), small
  and atomic; staged by explicit paths only (`git add <paths>` — never `-A`: local notes and
  personal agents live untracked in the tree).
- The `pharos-dev` run is settled: QA green, panel triaged, applicable design/UX gates passed. An unresolved gate blocks PR submission unless the
  user explicitly authorized a draft PR that states the blocker. Without a run, do steps 6.1–6.3 here.
- Gates re-run now, in this session, outputs in hand: `python3 harness/core/scripts/gate.py
  --changed origin/main --keep-going`, plus every CI job for the touched paths that the gates do
  not cover (the project's CI workflow names them).

## 2. Push and open

`git push -u origin <branch>`, then `gh pr create --base main --title "<conventional title>"
--body-file <file>`. A branch stacked on another unmerged branch takes that branch as `--base`
and says so in the body's first line. Update an existing PR with `gh pr edit --body-file`.

## 3. Description — the author's style

Open with one paragraph a reviewer who reads nothing else still gets the point from. Then:

```
## Problem       — what breaks or is missing, with observed evidence
## Root cause    — bug fixes only: the precise mechanism
## Change        — one bullet per logical change, measured numbers when sizing anything
## Verification  — what was actually run (commands, environments, numbers), not "tests pass"
## Notes         — deliberate non-goals, deferred items, follow-ups
```

- A change spanning several components carries an ASCII diagram of the public surface between
  them (handler → service → queries, page → API client → wire types) in the summary.
- Describe the change relative to the base branch, never to intermediate states of the branch:
  anything claimed removed or renamed must exist in the base (`git grep <id> origin/main`).
- Edit the body for a reviewer: plain English, every fact kept, no session jargon (a `mechanical`
  dispatch can do it; it needs no stronger model).
- End the body with the attribution line the session's instructions require.

### End-to-end evidence — every Tier 2–3 PR

Run it for real: use the project's local stack or the tracked development commands selected by
the runtime adapter, exercise the change end to end, and include in the body:

- UI-visible change: the design verifier's screenshots from the run (`<run-dir>/screens/`) of
  every state the change touches, both themes and phone width where layout changed; a state they
  do not cover → dispatch the `ui-design-verifier` to capture it, never a second capture of your
  own;
- server change: the request/response or log lines the change is about (`curl` with the token
  redacted), plus what the run did **not** prove.

Missing evidence is a review finding, not a formality.

## 4. Manual test plan

For a Tier 2–3 PR invoke the `manual-test-plan` skill and link the plan comment from the
`## Verification` section; a Tier 0–1 PR names its tier there instead.

## 5. CI

`gh pr checks <n>`. A code failure is fixed with a new commit; rerun an infrastructure failure only when its cause
is identified and resolved, never until a flaky check happens to pass.
Follow the session's PR-monitoring instructions rather than polling CI yourself.

## 6. Review round and the verdict comment

1. Own adversarial review of `git diff origin/main...HEAD`: tenancy, auth, error paths, what a
   wrong value does at runtime, sizing against real limits.
2. Reuse the `pharos-dev` panel's reports (perf-analyst, security-auditor, design review,
   ui-design-verifier, the project roles) for an unchanged diff; dispatch the missing lenses for
   a changed one.
3. Triage: apply accepted findings as new commits; reject bad ones with a reason.
4. Post ONE consolidated comment, `gh pr comment <n> --body-file …`: **Taken / Rejected /
   Deferred**, one line of rationale each, in plain review-notes tone.

Work still to be done (follow-ups, a manual-review list) goes one item per comment with a shared
prefix: GitHub cannot check off part of a comment.

**Never resolve a review thread you answered** — reply and leave it for the reviewer to close.

## 7. Report back

PR URL, CI state, what the verdict comment says, and anything left for the user.
