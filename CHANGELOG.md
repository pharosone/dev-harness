# Changelog

All notable changes to the core. Versions follow semver: a minor release may add overlay keys
with defaults; a major release may require an overlay change and says which.

## 1.1.1 — 2026-10-09

- `task_state.py init` works without `--spec`, as `pharos-investigate` and discovery need.
- `tripwire.py report` shows a dispatch without a tool count as unknown (never 0), a unit whose
  checkout is gone as `unchecked`, units that have only frozen tests, and survives an
  unreadable ledger line instead of failing the whole report.
- `xreview.py` counts and reserves a critical review under one file lock: concurrent reviews
  can no longer pass the cap together.

## 1.1.0 — 2026-10-09

- `pharos-investigate`: understand before acting — a bug's root cause, research of data or
  options for a decision, how unfamiliar code works; a report with evidence and confidence that
  hands off to `pharos-dev`, `pharos-discover` or the user.
- Depth mode: `pharos-investigate`, `pharos-discover` and `pharos-dev` (a bug with an unknown
  cause) ask `standard` or `deep`, the recommended one first with its signal. `deep` runs the new
  read-only `investigator` role on the new `deep-reasoning` profile (Opus `xhigh` / Astra
  `xhigh`) and gets a second opinion from the other vendor.

## 1.0.0 — 2026-10-09

- Risk-tiered development process (`pharos-dev`), requirements interview (`pharos-discover`),
  PR, review and manual-test skills.
- Deterministic tools: `guard.py` (PreToolUse for Claude Code and Codex), `tripwire.py`,
  `tier.py`, `gate.py` + `quiet.py`, `task_state.py`, `worktree.py`, `xreview.py` (cross-vendor
  review), `check_docs_drift.py`, `render_adapters.py`, `harness_sync.py`.
- Core + project overlay (`harness/project`), consumed as a git subtree at `harness/core`.
- Runtime modes: both vendors, Claude only, Codex only.
- Reviewer roles are goal-first: each builds the model of the change in front of it (threat
  model, cost model, users and states, false-green risks) and picks its techniques from it;
  project knowledge comes from the overlay as context, not as a checklist. The design verifier
  owns the screenshots: the orchestrator brings the application up and briefs it, the verifier
  captures every state into the run directory, and the gate and the PR reuse those files.
- `harness_sync.py pull` checks an update in a scratch worktree; the branch fast-forwards only
  when render, core tests and project tests are green.
- The guard protects the modules it imports and the vendored core (`harness/core/`: the user
  approves on Claude Code, denied on Codex).
- The CI pin reads every command of a `run:` line, `defaults.run` (working directory, shell),
  wherever they are declared, and counts a command that may be skipped or whose failure the
  shell ignores (`||`, `&&`, `cd &&` included) as gating only in the script's last `&&` list;
  a command after a background job (`&`) still gates.
- The xreview tests assemble their synthetic private-key headers at run time, so a project's
  secret scanner does not flag the vendored core.
- Skills and rendered roles name the overlay in use (`$HARNESS_PROJECT`);
  `project.py overlay` prints it.
