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
- **Prefix:** roles list only the tools they use; the browser stays with `ui-design-verifier`.
  Subagent cache TTL is 5 minutes (`subagentPromptCacheTtl`). Do not paste files into briefs:
  name paths.
- **Review calibration:** use `prompts/xreview.md` and the `pharos-dev` matrix: six fields
  `[Critical|Major|Minor] path:line — defect — likelihood/impact — reachable path — failure scenario — evidence: path:line`.
  Code-read evidence is mandatory; missing fields/evidence remain Minor until verified.
  Overlay `review.stage` defaults to `pilot`, invariants to empty. Severe/will or plausible
  blocks; severe/edge blocks only for a violated exact overlay invariant or production stage.
  Functional/will blocks; functional/plausible needs verified `workaround: none` to block;
  functional/edge and all cosmetic findings go to backlog. Optional trailing fields are
  `— workaround: none` or `— workaround: <specific workaround>` and
  `— invariant: <exact overlay invariant text>`. Labels never override the matrix.
  Ingest QA/panel reports before triage with `review_calibration.py record --run <run>
  --report <role-report> --milestone <m> --round 1|2 [--addressed <previous blockers>]`;
  it reads the active overlay. Distinct report paths/lenses share a round; rounds count once.
  Repeated reports in a round and returning to round 1 after round 2 are refused.
  Before fix-up, check each blocker against code (≤5 min), record the triage reason using
  `review_calibration.py triage`, and downgrade per the matrix. Minors never block: ≤15 min
  fixes join the fix-up; others go to `<run>/backlog.md`. Use `--run-dir <run> --milestone <m>
  --round 1|2`; `--addressed` implies round 2, and repeated `--section` preserves commas.
  At most two rounds per milestone across cross-review, QA and panel; round 2 checks fixes/new code
  and previous blockers only, and a new blocker must be severe and matrix-blocking. No automatic
  third round. All findings live in `<run>/review-ledger.jsonl`; G4/final reports show the
  calibration summary, backlog path, outcomes and `review_calibration.py fixture` feedback hook.
  `outcome --user-override block|backlog` may omit `--outcome`; an explicit user override alone
  does not fabricate a later outcome. `fixture --case` takes a sanitized JSON file path.
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
