# Claude Code adapter

Skills resolve through `.claude/skills/*`. The root `CLAUDE.md` imports `AGENTS.md`; generated
roles set `omitClaudeMd` and read the map once themselves. `.claude/rules` carry the path rules'
text; pass the rule paths to agents of other runtimes.

- **Questions:** `AskUserQuestion` within its batch limit, recommended option first and never
  treated as submitted; otherwise ask in the conversation.
- **Delegation:** the Agent tool with the generated role (`subagent_type`), model from the
  unit's profile (`model` parameter); with both runtimes, `balance.py pick` names the vendor,
  model and effort first. Feed it Claude's usage with `balance.py observe --from <log>` from
  any captured stream-json log (headless lanes' logs are read automatically). Agent
  definitions are read at session start: a session that changed them uses the previous
  generation until restarted — record that in `run.md`.
  Tag unit dispatches `[pharos task=<t> unit=<n>]`; the generated `PostToolUse(Agent)` hook runs
  `tripwire.py hook`. A background dispatch is not checked at launch: run
  `tripwire.py check --record` when its report arrives.
- **Events:** for tagged dispatches the generated `PreToolUse(Agent)` and `PostToolUse(Agent)`
  hooks write `dispatch.start` and `dispatch.end` to the run's `events.jsonl` (a background
  launch also links its agent id); nothing to call yourself. A background completion the hook
  does not see is ended with `tripwire.py spend <t> <n> --tool-uses N --dispatch <agent id>
  --status done|failed|timeout|cancelled`; run start and gates go through `task_state.py event`.
  Agent tokens stay null (the tool reports only the final request).
- **Lanes:** `lanes.py decide` picks the launcher. `subagent` is the Agent tool above;
  `headless` and `pane` go through `lanes.py start` (`claude -p --output-format stream-json`
  or `codex exec --json`, logs in `<run>/lanes/`), which records its own events. A pane needs
  `HERDR_ENV=1` and opens beside this session (a second one in a tab named after the task,
  another repository in its own tab), or in the run's dashboard tab once it is open; without
  herdr the lane runs headless. The agent limit is `project.json → lanes` (default three).
- **Dashboard:** `python3 harness/core/scripts/dashboard.py <task>` writes
  `<run>/dashboard.html` and prints its path; the hooks and `lanes.py` regenerate it after each
  event. Open it locally; it has no scripts and no network references. With `HERDR_ENV=1`,
  `lanes.py dashboard --task <task> --open` at run start opens the live tab `📊 <task>` (status
  and events views, project panes, the run's lane panes); `--close` closes its own panes.
- **Watch:** at run start arm `Monitor` with
  `python3 harness/core/scripts/watch.py --task <task>` as an unbounded command, using the
  maximum timeout the tool supports; re-arm it on expiry or any unexpected exit while the run is active. If Monitor
  is unavailable, run the watcher as a background process and read its output every turn and
  before going idle. Its lines and `watch` events require action in the same turn: read the
  actual dialog; answer questions you can decide from the spec and earlier authorizations;
  never answer trust/permission reviews; escalate material decisions to the user. Nudge or
  cancel frozen lanes; inspect exited/ended-no-report lanes; accept done reports. As part of
  report acceptance or handling an ended-no-report dispatch, run
  `python3 harness/core/scripts/watch.py --task <task> --ack <dispatch>` to close its terminal
  alert permanently. New work uses a new dispatch id; live input/freeze alerts cannot be acknowledged.
  For repeating product failures, run the product's own `watch.stop_command` from its checkout
  (or escalate if it is absent), preventing another retry while diagnosing. Tell the user in
  2–3 lines what was done; stay silent when no condition fires. Checks come from `project.json →
  watch`; no product stop or dialog answer is automatic in the script.
- **Prefix:** roles list only the tools they use; the browser stays with `ui-design-verifier`.
  Subagent cache TTL is 5 minutes (`subagentPromptCacheTtl`). Do not paste files into briefs:
  name paths.
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
- **Isolation:** native worktree isolation starts from the session's checkout, not the run's
  branch; use `harness/core/scripts/worktree.py add` instead. Durable reports stay in the run dir.
- **Browser:** use the session's browser tools; derive startup from tracked compose files and
  package scripts; check which checkout is served.
- **Hooks:** generated `.claude/settings.json` runs the Stop wrapper, `guard.py` before Bash,
  Read, Edit, Write and NotebookEdit, and the tripwire before and after Agent. `git push` triggers a
  permission prompt every time. Hooks are a guardrail; CI still runs every check.
- **Claude-only mode** (Codex unavailable): everything as usual except the review —
  `xreview.py --writer claude --reviewer claude` (Opus `high` in a fresh context, recorded as
  single-vendor); Codex workers are replaced by Claude dispatches on the same profiles.
- **Headless:** `claude -p` in a checkout loads its hooks and agents (not with `--bare`); the
  cross-review runner uses it with read-only tools when Codex wrote the code.

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
