# Codex adapter

Codex is a **worker** in this harness: it implements a unit, reviews another vendor's diff or
answers a science/maths question, then stops with a report. It is never the orchestrator or a
long-running owner of a feature: an owner session without user turns can run for a day and
spend a weekly limit before anyone sees a result. Skills resolve through `.agents/skills/*`
(`$pharos-dev`) or their `skills/<name>/SKILL.md`. `AGENTS.md` is loaded automatically: do not
read it again.

- **Codex-only mode** (Claude unavailable, or the user asks): Codex may orchestrate
  `pharos-dev`, within limits that keep it from becoming an owner session again:
  - orchestrator on `gpt-6.1-sol` `high` (never Astra); workers from `.codex/agents/*` on their
    profiles, each dispatched with a brief, a report file and ≤ 80 responses;
  - a taste gate at every milestone even if the spec lists fewer, and a stop with a gate card
    after at most 150 responses in one session or two hours without a user turn — whichever
    comes first; the next session resumes from `run.md`;
  - wait for a worker with the blocking wait, never a polling loop (each status call re-sends
    the whole context and a loop of them can spend a weekly limit);
  - after each worker report: `tripwire.py spend <task> <unit> --tool-uses N --dispatch <d>
    [--status …]` and `check --record`; at each milestone:
    `xreview.py --writer codex --reviewer codex`;
  - the guard: the trusted primary clone loads `.codex/hooks.json`; any worktree needs the `-c`
    override below.
- **Brief:** a role brief names the unit contract, the checkout, the paths, the report file and
  a budget of ≤ 80 responses. Finish by writing the report and replying with its path. A
  question you cannot resolve goes into the report as a question, not into a pause.
- **Roles:** `.codex/agents/*.toml` set model, effort and `sandbox_mode` per role — reviewers
  `read-only` (their final message is the report; the orchestrator saves it), writers and QA
  `workspace-write`. Permission to run tests is not permission to edit sources.
- **Models:** from `harness/core/adapters/models.json` (`gpt-6.1-sol` by default, `gpt-6-astra` for
  Tier-3 review, science and the `deep-reasoning` investigator, `gpt-6-luna` only with a deterministic check of its output). Check
  availability; never claim a model ran that the session did not resolve. With both runtimes,
  the orchestrator picks each worker's vendor inside its profile by `balance.py pick`, which
  reads Codex usage from the session files under `$CODEX_HOME` by itself.
- **Cross-review:** the orchestrator runs `harness/core/scripts/xreview.py`, which calls
  `codex exec --sandbox read-only` with the diff as context. As a reviewer: report only, never
  edit; a Critical/Major needs a failure scenario; end with the `VERDICT:` line.
- **Isolation:** a writer works in its own Git worktree (`harness/core/scripts/worktree.py add`).
- **Hooks:** `.codex/hooks.json` runs the shared Stop wrapper and `guard.py` before shell and
  patch tools — but Codex loads a project's `.codex/` layer only for a trusted path, and every
  worktree is a new, untrusted path, where project hooks do not run at all (the same guard passed
  with `-c` does). Launch every Codex worker in a worktree with the guard on the command line:
  `codex -c 'hooks.PreToolUse=[{matcher="Bash|apply_patch|Edit|Write",hooks=[{type="command",command="python3 <checkout>/harness/core/scripts/guard.py --runtime codex || true",timeout=10}]}]' …`.
  Trusting a path (`/hooks`) stays the user's decision; never bypass trust on your own.
  Hooks are a guardrail, not a boundary: the sandbox and CI still apply. There is no dispatch
  tripwire hook on Codex: the orchestrator runs `tripwire.py spend <task> <unit> --tool-uses N --dispatch <d>
  [--status …]` and `check --record` on each report.
- **Events:** with no hook, a Codex orchestrator records its own subagent dispatches in the
  run's `events.jsonl`: `task_state.py event <task> dispatch.start --id <d>:start --data
  '{"dispatch": "<d>", "role": …, "runtime": "codex", "launcher": "subagent", "work": "<unit>"}'`
  at launch, and at the end `tripwire.py spend <task> <unit> --tool-uses N --dispatch <d>
  --status done|failed|timeout|cancelled` (the spend line and `dispatch.end` together). Run
  start and gates go through `task_state.py event`. A count you do not have stays null.
- **Lanes:** `lanes.py decide --orchestrator codex` picks the launcher; `headless` and `pane`
  start through `lanes.py start`, which records `dispatch.start`/`dispatch.end` itself and
  carries the guard override into every Codex lane (headless and pane). Wait with `lanes.py
  wait`, the blocking wait, never a status loop. A pane needs `HERDR_ENV=1`; a `blocked` lane is
  the user's to answer. The agent limit is `project.json → lanes` (default three).
- **Dashboard:** `python3 harness/core/scripts/dashboard.py <task>` writes
  `<run>/dashboard.html` and prints its path; `lanes.py` and `tripwire.py spend --dispatch`
  regenerate it. Give the user the path at each gate. With `HERDR_ENV=1`, `lanes.py dashboard
  --task <task> --open` at run start opens the live tab `📊 <task>` (status and events views,
  project panes, the run's lane panes, which then start there); `--close` closes its own panes.

Verified references (2026-10-09): learn.chatgpt.com/docs — skills, agent-configuration/subagents,
hooks, non-interactive-mode, config-file/config-reference.
