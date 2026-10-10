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
