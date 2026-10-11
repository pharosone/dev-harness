# Codex adapter

Codex is a **worker** in this harness: it implements a unit, reviews another vendor's diff or
answers a science/maths question, then stops with a report. It is never the orchestrator or a
long-running owner of a feature: an owner session without user turns can run for a day and
spend a weekly limit before anyone sees a result. Skills resolve through `.agents/skills/*`
(`$pharos-dev`) or their `skills/<name>/SKILL.md`. `AGENTS.md` is loaded automatically: do not
read it again.

- **Profile authority:** resolve orchestrator and worker model/effort at dispatch time from
  `harness/core/adapters/models.json` deep-merged with `harness/project/models.json`.
  Never use personal memory or saved model preferences. Record the resolved profile/model/effort;
  router shadow recommendations stay advisory and do not replace actual static profile choices.
- **Lane reports and requests:** progress goes to `<report>.progress.md`; the final report
  goes to `<report>` only when work ends. For coordinator input, write
  `<run>/lanes/<work>.flag.md` (or `tripwire.py flag`); watch surfaces `needs-input flag`.
  If the run directory is outside writable roots, use `<report>.flag.md`. Native subagent
  events use the same watched channel. Never type into another agent pane. The coordinator
  resolves and removes answered flags.

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
  edit; use the calibrated six-field contract below; end with the matrix-derived `VERDICT:` line.
- **Review calibration:** use `prompts/xreview.md` and the `pharos-dev` matrix: six fields
  `[Critical|Major|Minor] path:line — defect — likelihood/impact — reachable path — failure scenario — evidence: path:line`.
  Code-read evidence is mandatory. Normalize backticks/bold and line ranges for parsing; retain
  explicit malformed/partial findings verbatim in `<report>.calibration.md` and the run ledger.
  Missing fields remain Minor until verified, but cannot approve a reviewer's block: exit 4
  requires triage; exit 1 is a computed blocker; only exit 0 approves. Fully verified matrix
  backlogs may override a reviewer's block to pass. Overlay defaults: `pilot`, empty invariants.
  Severe/will or plausible blocks; severe/edge blocks only for violated exact overlay invariant
  or production. Functional/will blocks; functional/plausible requires `workaround: none`;
  functional/edge and cosmetic backlog. Optional fields: `— workaround: none` or specific
  workaround, `— invariant: <exact overlay invariant text>`, `— id: <prior stable ledger finding ID>`.
  Ingest QA/panel before triage with `review_calibration.py record --run <run> --report <role-report>
  --milestone <m> [--round 1|2] [--addressed <previous blockers>]`; it reads the active overlay.
  Both CLIs leave `--round` unspecified by default: `--addressed` implies 2 only without an
  explicit round. `--round 1 --addressed <external-report>` imports context only, remaining round 1.
  Persisted xreview calls require explicit `--milestone`; standalone report-stem fallback is
  informational. Repeated `--section` preserves commas. Distinct lenses share at most two rounds;
  counts include active/completed logical rounds, not failed attempts. Failed/timeout reviewer
  attempts release reservations for at most 3 attempts of the same review input across `--out`
  changes; running/completed reviews cannot duplicate. Returning to round 1 after round 2 is refused.
  Round 2 checks fixes/new code and previous blockers only; match source path, reachable caller
  and failure scenario plus ledger triage, not title alone. Lines may move; optional `id` references
  the same prior stable finding. Known same-site but uncertain identity needs triage (exit 4).
  New blockers must be severe and matrix-blocking; no automatic third round.
  Before fix-up check each blocker against code (≤5 min), record triage reasons and downgrade
  per matrix. Minors never block: ≤15 min fixes join fix-up; others go to `<run>/backlog.md`.
  G4/final reports show calibration summary, triage needs, backlog path, outcomes and feedback hook.
  `outcome --user-override block|backlog` may omit `--outcome`; override alone fabricates no outcome.
  `fixture --case` takes a sanitized JSON file path.
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
  wait`, the blocking wait, never a status loop. A pane needs `HERDR_ENV=1`; read its blocked
  dialog; answer questions you can decide from the spec and earlier authorizations;
  never answer trust/permission reviews; escalate material decisions to the user.
  The agent limit is `project.json → lanes` (default three).
- **Dashboard:** `python3 harness/core/scripts/dashboard.py <task>` writes
  `<run>/dashboard.html` and prints its path; `lanes.py` and `tripwire.py spend --dispatch`
  regenerate it. Give the user the path at each gate. With `HERDR_ENV=1`, `lanes.py dashboard
  --task <task> --open` at run start opens the live tab `📊 <task>` (status and events views,
  project panes, the run's lane panes, which then start there); `--close` closes its own panes.
- **Watch (when orchestrating):** at run start run
  `python3 harness/core/scripts/watch.py --task <task>` as a background process with stdout
  captured under the run directory; read new lines every turn and before going idle, and
  restart it if it exits while the run is active. Alternatively run
  `python3 harness/core/scripts/watch.py --task <task> --once` at every turn and before going
  idle. This is one poll at turn boundaries, not a model-driven status loop. Act on every line
  in that same turn: read the actual dialog; answer questions you can decide from the spec and earlier authorizations;
  never answer trust/permission reviews; escalate material decisions to the user. Nudge or
  cancel frozen lanes; inspect exited/ended-no-report lanes; accept done reports. As part of
  report acceptance or handling an ended-no-report dispatch, run
  `python3 harness/core/scripts/watch.py --task <task> --ack <dispatch>` to close its terminal
  alert permanently. New work uses a new dispatch id; live input/freeze alerts cannot be acknowledged.
  Stop repeating product failures through the product's own
  `project.json → watch.stop_command` from its checkout (or escalate if missing), before
  diagnosing another retry. Tell the user in 2–3 lines what was done; silence when nothing
  fires. The script observes; it never sends answers or executes the stop command.

Verified references (2026-10-09): learn.chatgpt.com/docs — skills, agent-configuration/subagents,
hooks, non-interactive-mode, config-file/config-reference.

## Harness feedback

Offer once per distinct script error/docs contradiction, rule workaround/manual step,
session tool the harness should own, user-corrected profile/tier/gate decision, or process
complaint. At a natural pause after the unit/gate/turn, check the full `feedback.py list`
including declined/sent drafts; compare the problem even when its title differs. Use
`list --offerable` only for existing unoffered drafts. Create a harness-only draft, and use
`offer <slug>` to ask exactly: **Offer harness feedback
“<drafted title>” for your review?** Never interrupt a running decision. The user decides;
`decline <slug>` records a refusal and prevents repeated offers. Use the same `--task` or
`--run-dir` queue for every command (omit both when there is no run).

The upstream is public: no product data. `send` previews sanitized text and checks duplicates.
Show candidate issue links; `--not-a-duplicate` requires the user to confirm they are distinct;
`--yes` requires the user's explicit go for that text. Core fixes offer `harness_sync.py
propose --feedback-file <draft>` instead; review code and body before publication. Missing
`gh`/auth prints a bounded browser URL; long drafts require copying the sanitized preview
into the form and stay unsent. At each taste gate and run end, show
`feedback.py list --summary`: “2 harness feedback drafts waiting: …”. Full procedure and
privacy rules: README “Feedback”.

## Skill evals

A new skill, or a change to a skill's instructions, ships with at least one added or changed
scenario from its purpose or a recorded failure, with graders for the required behaviour.
The unit is not done without score with the skill, score without it (baseline), Δ, model,
cost and run date. Require Δ > 0 on every new or changed case and no regression on existing
cases beyond the overlay tolerance (default 0.05). A case whose Δ stays 0 does not test the
skill: fix the case. This covers core and product skills, including non-Claude skills.

Run `python3 harness/core/scripts/skill_evals.py run --skill <SKILL.md path> --model <model>
--run-dir <run> --base <unit base>`; add a cost ceiling with `--max-cost-usd <usd>`.
Evidence goes to `<run>/evals/<skill>.json` and the skill's eval history; quote the scores,
Δ, model, cost and run date in the unit report. Run the cheap gate
`python3 harness/core/scripts/skill_evals.py check --base <unit base>` before accepting the unit.

Codex-only evals: configure `project.json → skills.eval_command` as a paired Codex runner,
then use `skill_evals.py run --runtime codex --skill <SKILL.md> --model <actual model>
--run-dir <run> --base <unit base>`. This routes both core and product skills through that runner and retains
the same cheap gate, hashes, case acceptance and history format. Missing runner is an error.
