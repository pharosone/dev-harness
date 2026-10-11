---
name: manual-test-plan
description: "Use when a PR in this repository needs its hand-executable verification plan written or executed (\"test plan\", \"план ручного тестирования\", \"lets start test plan\", \"начнём тест-план\"); submit-pr invokes it for every PR."
---

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/manual-test-plan.md` (this skill's project specifics) before following
this procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it. Resolve task artifacts with `python3 harness/core/scripts/task_state.py locate
<task>`; use the returned absolute directory, including for an existing legacy run. `<run-dir>`
below means that resolved directory. Reuse existing user authorization; publish comments or
reviews only when the user explicitly requested it or explicitly invoked a skill authorizing
that action.

**Dispatch authority:** resolve every orchestrator/worker model and effort from
`harness/core/adapters/models.json` deep-merged with the active
`harness/project/models.json` (or `$HARNESS_PROJECT/models.json`). Record profile, model
and effort. Never select from personal memory or saved preferences; router shadow is
advisory and cannot replace the actual static profile choice. Lane progress goes to
`<report>.progress.md`; write final `<report>` only at the end. Requests use
`<run>/lanes/<work>.flag.md` or `tripwire.py flag`; never type into another pane.

The agent performs every automatable check before and after a human authorization/sign-in
step. When browser tools are available, the agent performs the bounded shadow-display check
after sign-in: the label explicitly says advisory, sits beside the actual static tuple,
and leaves actual dispatch unchanged. Human residual steps contain only actions the agent
cannot perform. Classifier experiments, pilots and model-quality measurements are outside
this UI/authorization plan.

# Manual test plan

Automated gates prove the code compiles and unit contracts hold; they do not prove the feature
works for a user in a browser against a real stack. Every Tier 2–3 PR
(`harness/core/scripts/tier.py`) gets a plan: what a person (or an agent driving a real
environment) does to see the change working and what they must observe. The PR is not done
until the plan ran once and the evidence hangs on the PR. Tier 0–1 PRs rely on CI and the
cross-review.

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

## Deriving the plan

List the externally observable surfaces the final diff adds or changes: endpoints and their
status codes, env vars, CLI commands, screens and their states, realtime events, log lines,
stored files. Each surface gets at least one end-to-end step. A pure refactor still gets a smoke
run plus the one behaviour it must preserve.

Steps run verbatim for someone with no session context: exact commands (the stack's start
command, `curl -H "Authorization: Bearer $TOKEN" …`), exact pages and clicks, exact expected
output. The cheapest real environment wins: the local stack with development auth; production
only for what local cannot show. Never reference files the repo does not track, and never
inline secrets.

## Plan shape

One PR comment opening with `**Manual test plan**`, tickable steps:

```markdown
**Manual test plan**

Environment: <stack, branch, seed data, required env keys>

- [ ] 1. <exact command or action> — expect: <observable result>
- [ ] 2. …

Cleanup: <how to leave the environment clean>

Executed: <date, by whom> — <link to the evidence comment, or "pending">
```

One observable expectation per step, under ~10 steps. Post with `gh pr comment <n> --body-file`,
link it from the PR's `## Verification`. After running it, tick the boxes, set `Executed:` and
attach the evidence. Ticking a plan without running it is falsifying a test record.

## Residual plan

Before review, the run is autonomous: API calls via `curl`, browser steps via the runtime adapter's available
browser tools. Only what an agent cannot do — a real sign-in with a password or 2FA, a
judgment call — goes into `<run-dir>/manual-plan.md`: per step, why the agent could
not do it, the exact action for the person, what the agent checks before and after.

## Executing with the user ("lets start test plan")

1. Do every part of a step you can yourself first.
2. Ask the user for exactly one action at a time, in one or two lines; never ask for a secret in
   chat.
3. After the user says done, verify the result yourself (page text, API, SQL, logs) and record
   the evidence next to the step.
4. A failed step: stop, show observed vs expected, ask whether to retry, skip or stop.
