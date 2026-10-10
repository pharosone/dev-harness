# Changelog

All notable changes to the core. Versions follow semver: a minor release may add overlay keys
with defaults; a major release may require an overlay change and says which.

## 1.5.0 — 2026-10-10

- Review fixes: `watch.py --ack D` permanently closes accepted/handled terminal lane alerts;
  live alerts cannot be acknowledged. Only Herdr status or an agent working indicator suppresses
  freeze checks; prose/prompt quotations from working agents do not trigger input alerts.
  Transient poll/lane errors are logged and retried; pane registration is protected while its
  recorded launcher is alive (legacy states have a bounded grace window).
- Check timeouts handle an exit race and bounded stdout draining; optional JSON `{key, message}`
  anomaly lines suppress changing counters by stable identity. HTML keeps the latest 50 watch
  events and does not regenerate for un-emitted frozen-minute changes. Full events stay on disk.
- Blocked-lane rule reconciled with the owner amendment, skill, adapters and wait messages:
  read the dialog, answer questions decidable from the spec and earlier authorizations,
  never answer trust/permission reviews, and escalate material decisions to the user.

- `watch.py --task T [--once] [--every S] [--quiet-repeat S]` watches the run's lane dialogs,
  blocked states, unchanged non-working screens, exits and completed/missing reports, plus
  overlay `watch.checks` shell commands. Cadence, open conditions and repeat suppression live
  in `<run>/lanes/_watch.json`; cleared conditions re-arm. No Herdr: persisted dispatch state
  and product checks still work. Unknown watch/check keys and bad values exit 2.
- Every emitted line becomes a locked `watch` event. The Herdr events pane and HTML dashboard
  show those lines; both status views show `Watch: ✅ quiet` / `Watch: 🚨 N open`.
- `pharos-dev` and both adapters arm the watch at run start (Claude Monitor, re-armed on expiry;
  Codex background process or one poll per turn). Each condition requires an action in the
  same turn: answer spec-resolved questions, escalate material and permission decisions,
  recover frozen/exited lanes, accept reports, stop repeating product failures through the
  product's `watch.stop_command`, and tell the user what was done. The script is observational.
## 1.6.0 — 2026-10-10

- Public harness feedback: `scripts/feedback.py draft/list/offer/decline/send` queues local
  harness-only drafts, remembers offers and declines, previews sanitized titles/bodies,
  checks open/closed issue duplicates and creates an issue only with user approval. Missing
  `gh`/auth prints a prefilled URL without marking the draft sent.
- Mandatory redaction reuses the dashboard/xreview secret patterns, scrubs product/overlay
  names, non-core absolute paths, emails, IPs and private URLs, and refuses unresolved secret
  patterns. Optional `project.json → feedback.redact` adds literal private terms; no required
  overlay change.
- Local vendored-core edits offer `harness_sync.py propose --feedback-file <draft>` instead;
  the small optional attachment extension links the reviewed draft in the PR body and
  sanitizes the PR title/body, and records the URL after authorized publication. Preparing
  feedback never publishes code.
- `pharos-dev`, `pharos-discover` and both runtime adapters offer feedback once per distinct
  harness failure, workaround, session tool, user-corrected decision or process complaint at
  a natural pause. `pharos-dev` lists waiting drafts at every taste gate and run end.
- Review fixes: sanitize redirected/tagged paths while preserving placeholders and link
  punctuation; redact checkout names at word boundaries while preserving harness terms;
  allow `send --issue` without subtree history and user-confirmed `--not-a-duplicate` with
  candidate links. Keep secret prose usable, distinguish Unicode/truncated draft slugs,
  refuse declined/sent PR attachments, and tighten the decline eval grader.
- Second review fixes: redact product/checkout names inside separated or digit-delimited
  tokens without changing ordinary words or harness vocabulary; inspect declined/sent
  drafts before drafting a reworded complaint; bound browser fallback URLs and direct
  long evidence to the full sanitized preview.
- Six owner-reported examples are synthetic test fixtures, never real issues. Renumber this
  entry at merge if the parallel release branches require a different version.

## 1.7.0 — 2026-10-10

- Every new or changed core/product skill ships with purpose/failure-based cases and paired
  measured scores, positive new-case Δ, model, cost, date and stored history; existing cases
  may regress at most 0.05 by default. Discovery includes "eval cases + Δ" in Done when;
  both runtime adapters and implementer briefs carry the same acceptance rule.
- New stdlib `skill_evals.py check/run/report`: cheap CI checks skill and case hashes,
  case changes, deltas and historical scores; Claude runs isolated paired plugin evals,
  while products can supply a Hermes/Codex runner via validated `project.json → skills`.
- Two new product-skill acceptance scenarios cover pharos-dev and pharos-discover. Existing
  coverage gaps remain follow-ups. No required overlay migration. Renumber at merge if
  parallel release branches require a different version.
- Review fixes: gate and hash the complete skill directory and transitive declared inputs;
  record a schema-2 input manifest, reject input changes during evaluation, compare regression
  only for identical cases, and require an explicit run base. External runners receive exact
  registered case paths in an environment manifest. Shared-context changes require purpose
  coverage and fresh measurements for all seven core skills; their instructions are unchanged.

## 1.8.0 — 2026-10-10

- Calibrated review acceptance: likelihood × impact matrix, code-read evidence and reachable
  scenarios; pilot/empty-invariant defaults, triage before fix-up and two rounds per milestone.
  Round 2 checks fixes/new code and previous blockers; only new severe matrix blockers can block.
- Cross-review accepts repeated `--section`, `--run-dir`, `--milestone`, `--round`; all findings
  enter `review-ledger.jsonl` (QA/panel reports use `review_calibration.py record` and share
  rounds), Minors go to `backlog.md`. Calibration records triage and observed outcomes, reports
  across runs, and exports sanitized feedback fixtures; user overrides need no invented outcome.
  Roles, runtime adapters, `pharos-dev` G4/final reports and a mixed-triage eval use this scale.
- Review parsing normalizes Markdown/line ranges and retains malformed claims verbatim;
  exit 4 requires triage. Prior blockers match path/caller/scenario plus ledger triage or a
  prior finding ID. Persisted milestones are explicit; failed reviewer attempts release rounds
  for bounded retry (three attempts per input). Explicit round 1 keeps addressed context in round 1.

## 1.3.0 — 2026-10-10

- `lanes.py`: the herdr agent limit is configurable — `project.json → lanes`
  `{"herdr_max_agents": N, "count": "workspace" | "run"}` (defaults 3 and `workspace`, as
  before); `count: "run"` counts only the live agents the run started, so the user's own idle
  sessions no longer use up the run's panes. Any other key or value is exit 2.
- Run dashboard tab: `lanes.py dashboard --task T --open` opens one herdr tab per run,
  `📊 <task>`, recorded in `<run>/lanes/_herdr.json`. Left column (58%): `📊 status` (units,
  lanes running/blocked/stale with herdr's live status, pending gates, open tripwires, vendor
  shares and latest picks; every 30 s), `🧾 events` (a formatted tail of `events.jsonl`) and
  the overlay's project panes; right column: the run's lane panes. Live pane lanes of the run
  move there when it opens. `--close` closes only the dashboard's own panes (a live lane's pane
  stays); without herdr the command is a no-op and `dashboard.py`'s page stays the fallback.
  New script `dashboard_herdr.py` (geometry, placement, the `view status|events` loops).
- Lane placement: with the dashboard open, `lanes.py start` puts pane lanes into its right
  column, reusing a run's pane whose lane ended and whose shell is idle again (directory and
  worker environment typed in, an earlier lane's variables unset) before splitting the last one
  down, and balances the column's heights. Every lane pane is labelled
  `🤖 <work> · <runtime> <model>`. Panes the run did not create are never closed, reused or
  moved.
- Optional overlay key `project.json → dashboard` `{"panes": [{"label", "command", "zone":
  "status" | "events" | "side"}]}`: project panes (a product's live status loop, a log tail) in
  their zone; unknown keys are exit 2.
- `pharos-dev` opens the herdr dashboard at run start and points to it at gates; the Claude and
  Codex adapters describe it. No required overlay change.

## 1.2.0 — 2026-10-10

- The critical cross-review cap is five per run (was four); fix-up and review rounds alternate
  without asking the user until the review passes or the cap is used up.
- Events log: one append-only `<run>/events.jsonl` per run (`run.start`, `dispatch.start`,
  `dispatch.end`, `gate.open`, `gate.answer`, `compare.*`), written by `task_state.py event`
  under a file lock; a repeated id is skipped, an unreadable line is skipped and counted.
- Claude dispatch events: the generated `PreToolUse(Agent)` and `PostToolUse(Agent)` hooks
  record tagged dispatches; a background launch links its agent id; `tripwire.py spend
  --dispatch ID --status S` ends a dispatch by hand (Codex, background completions).
- `lanes.py`: `decide` picks subagent, headless CLI or herdr pane by a tested rule;
  `start/status/wait/result/cancel` run headless and pane lanes the same way and record their
  events. At most three live herdr agents; a `blocked` lane is surfaced, never answered; panes
  the run did not create are never closed or reused; Codex lanes carry the guard.
  `--max-budget-usd` caps a headless Claude lane.
- `lanes.py start --capability candidate --scratch DIR`: one profile on both vendors for a
  compare candidate — a shell and the source, writes only in DIR. The candidate works in a
  candidate snapshot: an independent shallow clone of HEAD plus the working changes (no remote,
  no alternates, no hardlinks), so a relative write never reaches the source. Claude runs under
  its Bash sandbox (fail closed) with `--add-dir DIR`; Codex under `--sandbox workspace-write
  --add-dir DIR`. Reads are not confined; independence rests on the brief and is stated in the
  capability profile. `lanes.py snapshot-cleanup --task T` removes the snapshots of ended
  candidates.
- `compare.py design`: two competitors write approach memos from one frozen interview record;
  an arbiter that wrote neither compares them twice, labels anonymised and order swapped
  (`arbiter unstable` on a flip). `compare.py investigation`: two independent reports, a
  cross-vendor critique of each and a disagreement map; a disputed answer means low confidence.
- `dashboard.py`: the run as one self-contained local page (`<run>/dashboard.html`) — running
  and stale lanes, pending gates, tripwires, spend per unit (unknown stays unknown), compares.
- Vendor balancing: `balance.py usage` reads each vendor's usage windows (Claude from captured
  stream-json logs and `observe --from LOG`, Codex from its session files, `usage set` by hand;
  no data is unknown, never a guess) and `balance.py pick --task --profile --role
  [--not-runtime]` picks a worker's vendor inside one profile: a window used at `stop_at`
  excludes a vendor, a projected run-out (after `min_elapsed` of the window) steers work away
  without ever forcing exit 6 alone, each vendor otherwise keeps `min_share` of the dispatches,
  and `hysteresis` keeps the previous pick. Exit 6 when every allowed vendor is excluded: ask
  the user. Each pick is a `balance.pick` event. `pharos-dev` picks every writer and reviewer
  this way when both runtimes are available; the dashboard shows each vendor's windows, runway,
  share and the latest pick per role. Optional overlay key `project.json → balance`.
- Skills: `pharos-discover` and `pharos-investigate` offer compare next to the depth mode
  (`off` by default, recommended only with its signal); `pharos-dev` starts every worker by
  `lanes.py decide`, records events and opens the dashboard at gates. Evals `compare-offer`,
  `compare-declined`, `lanes-boundary`.
- No required overlay change: `compare.py` reads the overlay's `models.json` as `xreview.py`
  does; the architecture files below are optional.
- `architecture-reviewer`: a new core role (read-only, `critical-review`) that joins the
  `pharos-dev` panel on every Tier 2–3 milestone, not only the PR. From the overlay's
  `architecture.md` and `architecture.json`, the spec's `## Placement` and the diff it checks what
  `arch_check.py` cannot see: the right module for each new piece, one responsibility per unit,
  names, no second implementation, no layer doing another's job; a deviation from Placement is a
  finding unless the spec was amended. The milestone runs `arch_check.py --base <milestone base>`
  first when the project has an `architecture.json`.
- `pharos-discover`: every spec carries `## Placement` — for each new or moved piece, its layer,
  module and file and why — drafted from the architecture map; "not applicable: <reason>" for a
  harness-only or docs-only spec. `pharos-dev` requires it.
- `design-review` builds its layer stack from the same map (`architecture.md`,
  `architecture.json`), so the PR-level pass and the milestone lens agree.
- Overlay: optional `architecture.json` (read by `arch_check.py`; without it the check is off)
  and `architecture.md` (the map in prose, read by the reviewer, `design-review` and discovery),
  documented in `docs/overlay.md`.

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
