# Claude Code adapter

Skills resolve through `.claude/skills/*`. The root `CLAUDE.md` imports `AGENTS.md`; generated
roles set `omitClaudeMd` and read the map once themselves. `.claude/rules` carry the path rules'
text; pass the rule paths to agents of other runtimes.

- **Questions:** `AskUserQuestion` within its batch limit, recommended option first and never
  treated as submitted; otherwise ask in the conversation.
- **Delegation:** the Agent tool with the generated role (`subagent_type`), model from the
  unit's profile (`model` parameter). Agent definitions are read at session start: a session
  that changed them uses the previous generation until restarted — record that in `run.md`.
  Tag unit dispatches `[pharos task=<t> unit=<n>]`; the generated `PostToolUse(Agent)` hook runs
  `tripwire.py hook`. A background dispatch is not checked at launch: run
  `tripwire.py check --record` when its report arrives.
- **Prefix:** roles list only the tools they use; the browser stays with `ui-design-verifier`.
  Subagent cache TTL is 5 minutes (`subagentPromptCacheTtl`). Do not paste files into briefs:
  name paths.
- **Isolation:** native worktree isolation starts from the session's checkout, not the run's
  branch; use `harness/core/scripts/worktree.py add` instead. Durable reports stay in the run dir.
- **Browser:** use the session's browser tools; derive startup from tracked compose files and
  package scripts; check which checkout is served.
- **Hooks:** generated `.claude/settings.json` runs the Stop wrapper, `guard.py` before Bash,
  Read, Edit, Write and NotebookEdit, and the tripwire after Agent. `git push` triggers a
  permission prompt every time. Hooks are a guardrail; CI still runs every check.
- **Claude-only mode** (Codex unavailable): everything as usual except the review —
  `xreview.py --writer claude --reviewer claude` (Opus `high` in a fresh context, recorded as
  single-vendor); Codex workers are replaced by Claude dispatches on the same profiles.
- **Headless:** `claude -p` in a checkout loads its hooks and agents (not with `--bare`); the
  cross-review runner uses it with read-only tools when Codex wrote the code.
