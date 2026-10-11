---
name: pharos-dev
description: "Use when implementing, fixing or refactoring anything in this repository beyond a one-line edit — executing an approved spec, a feature, a bugfix — and on follow-ups (\"сделай\", \"реализуй\", \"исполни спеку\", \"почини\", \"продолжи\", \"перезапусти ревью\", \"redo the QA\"). A review-only request runs the review step alone."
---

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/pharos-dev.md` (this skill's project specifics) before following this
procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it.

**Dispatch authority:** resolve every orchestrator/worker model and effort from
`harness/core/adapters/models.json` deep-merged with the active
`harness/project/models.json` (or `$HARNESS_PROJECT/models.json`). Record profile, model
and effort. Never select from personal memory or saved preferences; router shadow is
advisory and cannot replace the actual static profile choice. Lane progress goes to
`<report>.progress.md`; write final `<report>` only at the end. Requests use
`<run>/lanes/<work>.flag.md` or `tripwire.py flag`; never type into another pane.

# pharos-dev — deliver an approved spec

**Goal:** every "Done when" row of the spec holds, proven by commands run in this session,
for the least spend that still covers the risk. The user steers at taste gates; between gates
deterministic checks steer. You orchestrate: keep the run file, dispatch writers and reviewers,
read their evidence, own the final report.

## Start

- **Spec.** It needs `Requirements interview`, the section of every project interview row
  (`harness/project/project.json → interview.extra_rows`, see `pharos-discover`; each may say
  "not applicable: <reason>"), `Placement` (see `pharos-discover`), TDD units with the paths
  each may change, and `Taste gates`.
  A missing required section → `pharos-discover` for that gap, reusing answers already given.
  A spec older than taste gates → propose the default gates (first end-to-end slice, each
  milestone, before the PR) with the start question.
- **A bug whose cause is not established** (no reproduction yet, intermittent, a fix that did
  not hold): `pharos-investigate` first, and its reproduction becomes the unit's failing test.
  Its mode goes into the start question: `standard` (you investigate) or `deep` (an
  `investigator` on `deep-reasoning` — Opus `xhigh` / Astra `xhigh` — plus a second opinion
  from the other vendor), recommended first with the signal: "`deep` (Recommended) — the
  failure is intermittent". A bug with a known cause goes straight to the unit loop.
- **Runtime mode.** Both vendors by default; Claude-only or Codex-only when one is unavailable
  (`harness/core/README.md`, «Runtime modes»). Record the mode in `run.md`. Codex-only: the
  orchestrator runs on GPT-6.1 Sol `high` (never Astra), stops at a gate at every milestone and
  after ≤ 150 responses or two hours without the user, and reviews with `--reviewer codex`
  (Astra reviews Sol, marked single-vendor); details in `harness/core/adapters/codex.md`.
- **Settings.** Profiles come from `harness/core/adapters/models.json` with the overrides in
  `harness/project/models.json` when present (table in the README); record the resolved model
  and effort. Commits follow the user's instruction; without one, leave changes unstaged. Push
  only on the user's request — the guard asks them every time.
- **Run state.** `python3 harness/core/scripts/task_state.py init <task> --spec <spec>` (or
  `locate` to resume: read `run.md`, check branch and SHA, continue at the first unit not
  `done`). Every report, red log, gate card and spend ledger lives in that directory, and so
  does `events.jsonl`, whose first event is the run's start: `task_state.py event <task>
  run.start --data '{"workflow": "dev", "depth": "<mode>", "compare": "off", "runtime_mode":
  "<mode>"}'`.
- **Run dashboard (herdr).** With `HERDR_ENV=1`, right after `run.start` open the run's tab:
  `python3 harness/core/scripts/lanes.py dashboard --task <task> --open` (`📊 <task>`: live
  status and events on the left, the run's lane panes on the right; project panes from
  `project.json → dashboard`). It changes only where a `pane` decision opens, never the
  decision: short report-only work on your own vendor is still a subagent (your agent tool),
  not a pane. Without herdr it is a no-op.
- **Run watch.** At run start arm `python3 harness/core/scripts/watch.py --task <task>`:
  Claude Code uses `Monitor`, unbounded with the tool's maximum timeout, and re-arms on expiry
  or any unexpected exit while the run remains active;
  if unavailable, use a background process and read its output every turn and before going
  idle. Codex uses a background process with captured output read at those boundaries, or
  `watch.py --task <task> --once` at every turn and before going idle. Restart a background
  watcher that exits while the run is active. Stop the watcher when the run ends. Product
  checks and the product's own stop command come from `project.json → watch` (see
  `harness/core/docs/overlay.md`); state and repeat suppression survive re-arming.

## Act on watch output

Every emitted line gets an action in the same turn. `LANE … needs-input`: read the dialog,
answer questions you can decide from the spec and earlier authorizations; never answer trust/permission reviews;
escalate material decisions to the user. A watch category is an alert, not the question text:
read the actual dialog and those authorizations before deciding. `frozen`: inspect, nudge or cancel the lane. `exited` or
`ended-no-report`: inspect its logs and recover or report the failed dispatch. `done`: read and
accept the report promptly, with the unit's checks and spend recording. As part of acceptance,
or after recovering/handling an `ended-no-report` dispatch, run
`python3 harness/core/scripts/watch.py --task <task> --ack <dispatch>`. Acknowledgment closes
that dispatch's terminal alert permanently; new work must use a new dispatch id. It cannot
hide needs-input or frozen conditions. For a `CHECK` indicating
a repeating failure or retry loop, stop the product's live process using its own
`watch.stop_command` from the product checkout before another retry; when no stop command is
configured, escalate immediately for the product's stop procedure. A repeated generic check
failure needs diagnosis; determine whether live work is retrying before stopping it. Report the
action to the user in 2–3 lines. Silence is fine when nothing fires. The watcher records
observations and never answers dialogs or stops the product itself.

## Tier decides the process

Classify each unit with `python3 harness/core/scripts/tier.py --base <unit base>` after it is
written (and from the spec's paths before); the stricter of the two wins. Which project paths
fall in which tier is set in `harness/project/tiers.json`.

| Tier | Writer profile | Tests | Review | QA | Panel |
|---|---|---|---|---|---|
| 0 docs/demo, ≤10 lines | `routine` or `mechanical` | existing | none | none | none — CI only |
| 1 tests/lockfiles/mechanical, ≤100 lines | `routine` | red→green in the same dispatch when a failing test exists | cross-vendor at the milestone | none | none |
| 2 everything else | `implementation` | red log → freeze → green | cross-vendor at the milestone | one fresh pass per milestone/PR (`routine`) | architecture lens on every milestone; design review once per PR; UI lens on UI changes |
| 3 auth, tenancy, storage, migrations, config, CI/CD, deploy, harness | `critical-review` | as Tier 2 | cross-vendor at the milestone on `critical-review` (Astra reviews Claude) | `critical-review` | + security and perf lenses; project roles whose condition holds |

**Project roles.** `harness/project/project.json → roles` lists the project's own roles: their
manifest fields and an optional `panel` condition. A role joins the panel as one more read-only
lens when every key of its condition holds: `min_tier` — the unit's tier is at least this;
`paths_exist` — at least one of these paths exists in the checkout. A role without `panel`
works only where an interview row names it (`pharos-discover`).

Refactor is a Tier 2–3 step for the `simplifier` (`critical-review`) when a unit touched more
than three files or a review named duplication, plus one cross-unit pass per milestone;
Tier 0–1 writers tidy their own diff.

## Vendor balance

**Goal:** both vendors last the run at the same quality. The profile, not the vendor, sets the
power — the same profile on both vendors; the pick only chooses which vendor runs it.

- With both runtimes available, pick every writer and reviewer (QA, lens, investigator) before
  `lanes.py decide`: `python3 harness/core/scripts/balance.py pick --task <task> --profile <profile> --role <role>`;
  a review that must be on the other vendor adds `--not-runtime <writer's runtime>`. Dispatch
  with the returned `runtime`, `model` and `effort`; record the pick's reason in `run.md` (the
  pick is also a `balance.pick` event on the dashboard).
  Exit 6 — every allowed vendor is at its stop line — ask the user; never fall back silently.
  Single-vendor modes and compare candidates (one per vendor) take no pick.
- The pick: a vendor with a current window used at or above `stop_at` is out. One projected to
  run out before its reset (runway < 1, counted once `min_elapsed` of that window has passed)
  is steered away from, even below its minimum share, but that alone never makes exit 6.
  Otherwise each vendor keeps `min_share` of the run's dispatches and the larger runway wins,
  with hysteresis. Keys and defaults: `project.json → balance` in the overlay doc.
- Usage: Codex's is read automatically, and so are the run's headless Claude logs. Before the
  first pick of a session you may feed Claude's with `balance.py observe --from <a captured
  stream-json log>`. Unknown usage counts as neutral, and the reason says so.

## Unit loop

Frozen tests allow only verified automatic amendments: Python AST-equivalent cleanup of
provably unused imports (collected tests/fixtures and unknown bindings remain protected), or
setup-only repair before recorded first green with unchanged assertion bytes and verified
setup-error red evidence. Re-run `task_state.py freeze --red-log` to archive and log the
reason. All other edits require coordinator approval. To authorize named list/tuple/set
literal additions, pin `task-state append-only: tests/test_tools.py:EXPECTED` in the initial
brief and pass `freeze --brief <brief>`; existing elements and all other AST nodes stay
unchanged. Immediately record the first passing test output using
`task_state.py green <task> <unit> --green-log <file>`; `verify` alone is not green evidence.
Never introduce skip/xfail workarounds; setup bodies with these controls stay protected.
Empty/all-skipped or expected-failure-only runs cannot establish first green.

1. `python3 harness/core/scripts/tripwire.py scope <task> NN --base HEAD [--turn-budget N] <spec globs>`
   in the unit's checkout. Parallel units only when their globs do not overlap, each in
   `python3 harness/core/scripts/worktree.py add <name> [--deps]`; one writer per checkout.
2. Dispatch the writer. The description starts with `[pharos task=<task> unit=NN]` (the
   `PostToolUse(Agent)` hook records spend and runs the tripwires). The brief quotes the unit
   contract from the spec and names the checkout, the paths, the rule files, the run directory
   and the report path. The implementer role defines the dispatch itself (tests → red log →
   `task_state.py freeze --red-log` → code → `verify` → `gate.py` →
   `task_state.py green --green-log` immediately after the first passing test command).
   Name `<report>.progress.md` for progress and `<run>/lanes/<work>.flag.md` for questions;
   never write the final report early or type into another pane.
   The writer's vendor, model and effort come from `balance.py pick` (Vendor balance above).
   How it starts — subagent, headless or a pane — is `lanes.py decide`'s answer
   (Lanes below). Codex is a worker, never the owner: its brief ends with a report file and a
   ≤ 80-response budget; you run `tripwire.py spend <task> NN --tool-uses N --dispatch <d>
   [--status …]` and `check --record` on its report.
3. Accept on evidence, not on the summary: `tripwire.py check` clean, `task_state.py verify`
   passes, the report quotes gate output from this dispatch. Re-run a gate yourself only when the
   evidence is missing or contradicts the claim.
4. Commit if authorized (explicit paths), `tripwire.py close <task> NN`, mark the row `done`.
   A flag you decided on is archived with `tripwire.py resolve <task> NN "<decision>"`.

## Lanes and events

**Goal:** each worker starts the way its work needs, and the run's `events.jsonl` says who did
what, so the dashboard and a resuming runtime see it without a living session.

- **Decide** every dispatch (writer, QA, lens, investigator) by the rule, not by habit:
  `python3 harness/core/scripts/lanes.py decide --role R --runtime claude|codex --orchestrator
  claude|codex --minutes N [--needs-user] [--outlives-turn] [--repo <checkout>] [--tier N]`.
  `subagent` → your own agent tool, tagged `[pharos task=<task> unit=NN]`; `headless` or
  `pane` → `lanes.py start` with the same signals plus `--task --work NN --model --effort
  --brief <file> --report <path> --checkout <dir>`. A pane needs `HERDR_ENV=1`; without it the
  same lane runs headless.
- **Work the lane** with `lanes.py status`, `wait` (blocking; never a polling loop), `result`
  (report and usage) and `cancel`. `start` exit 3: dispatch it as a subagent; exit 4
  (`queued`, at the herdr agent limit: three by default, `project.json → lanes`): start it
  again after a lane ends. `wait` exit 5: the
  lane is `blocked` — never answer trust/permission reviews yourself. Read its dialog and
  apply «Act on watch output»: answer questions you can decide from the spec and earlier authorizations;
  escalate material decisions to the user.
- **Panes:** the run's own panes stay open after the work;
  panes the run did not create are never closed or reused, and `cancel` refuses a pane that
  is no longer the run's. With the dashboard open, a lane pane whose agent has exited is
  reused for the next lane; at the end of the run `lanes.py dashboard --task <task> --close`
  closes the dashboard's own panes.
- **Events:** `lanes.py` records `dispatch.start` and `dispatch.end` for what it starts. A
  Claude subagent is recorded by the generated hooks; a Codex subagent, or a background
  completion the hook did not see, by you: `task_state.py event <task> dispatch.start --id
  <d>:start --data '{"dispatch": "<d>", "role": …, "launcher": "subagent", "work": "NN", …}'`
  at launch and `tripwire.py spend <task> NN --tool-uses N --dispatch <d> --status
  done|failed|timeout|cancelled` at its end (`<d>` may be the agent id the launch returned).
  Usage you do not have stays null; never estimate it.

## Cross-vendor review — at most two rounds per milestone

A unit is guarded by tests, red evidence and tripwires; review the **milestone** diff so sibling
units are visible. Before the milestone gate run:
`python3 harness/core/scripts/xreview.py --base <milestone base> --writer claude|codex --spec <spec>
--section "<heading, including any comma>" --section "<another heading>" --run-dir <run>
--milestone <m> --round 1 --ledger <run>/xreview.jsonl --out <run>/reports/<m>-xreview.md`.
Use repeated `--section` for exact headings; legacy `--sections` still splits on commas.
Persisted calls (`--run-dir` or `--ledger`) require an explicit stable `--milestone <m>`;
there is no shared default. Standalone reviews may use the report stem as an informational fallback.
`--round` defaults to unspecified: it resolves to 2 with `--addressed`, otherwise 1.
An explicit round always wins.
An explicit `--round 1 --addressed` imports earlier external findings as context only:
`xreview.py --base <base> --writer claude|codex --run-dir <run> --milestone <m> --round 1
--addressed <external-report> --out <run>/reports/<m>-xreview.md`. It remains a first-round review.
The tier comes from `tier.py`; Astra only for Tier-3 paths, otherwise GPT-6.1 Sol or Claude Opus.
Before the PR, review only changes since the last reviewed milestone; skip an empty diff.
Only exit 0 approves review. Exit 1 means a computed matrix blocker; exit 4 means findings need
triage (malformed/partial blocking claims or uncertain prior-blocker identity), not a verified
blocker. Both require the triage step before dependent work. Exit 2/3 (refused, failed reviewer,
timeout or no verdict) is not approval: record the review as not performed. Fully verified matrix
backlogs may override a reviewer's block to pass; an unverified blocking claim cannot. A diff over 200 KB is split by area with
`--paths`; each part lists siblings. Splitting areas or changing lenses does not reset rounds.
A failed/timeout reviewer attempt releases its reservation for a bounded retry: at most three
attempts for the same review input, including retries with a different `--out`. Running or completed
reviews cannot duplicate. Reports count active/completed logical rounds; failed attempts do not
consume a successful round. Do not retry a completed review to evade its result or round cap.

### Review calibration and triage

Use the overlay's `project.json → review`: `stage` defaults to `pilot`, `invariants` to empty.
Every finding is one Markdown list item with six em-dash-separated fields, parsed by the runtime:
- [Critical|Major|Minor] path:line — defect — likelihood/impact — reachable path — failure scenario — evidence: path:line

Reachable path = real caller/route; scenario = specific input/state → wrong result; evidence =
code read in this review. Tests/measurements supplement code-read evidence. Optional trailing
fields: `— workaround: none` or `— workaround: <specific usable workaround>`;
`— invariant: <exact overlay invariant text>` for a proven violation, never an invented one;
`— id: <prior stable ledger finding ID>` only to reference that prior finding.
The parser normalizes backticks/bold and line ranges. Explicit malformed/partial findings are
retained verbatim in the sidecar `<report>.calibration.md` and `<run>/review-ledger.jsonl`.
Missing ratings, reachable path, scenario or code-read evidence remain Minor until verified;
they never establish approval of a reviewer's block. Exit 4 requires triage before proceeding.

Likelihood in THIS product: `will` = main path within days; `plausible` = documented path users
sometimes take; `edge` = rare combination, race window, unusual environment or operator mistake.
Impact: `severe` = unapproved/wrong-recipient outbound action, burned limits/credits, account-ban
risk, external secrets/personal-data leak, data loss, or a guard/check silently stops working;
`functional` = recoverable feature failure/visible wrong result; `cosmetic` = presentation only.

| Impact / likelihood | will | plausible | edge |
|---|---|---|---|
| severe | block | block | block only for violated overlay invariant or production stage; else backlog |
| functional | block | block only with verified no workaround; else backlog | backlog |
| cosmetic | backlog | backlog | backlog |

Blockers: Critical for severe, Major for functional; backlog: Minor. Labels never override the
matrix. Plausible/functional requires verified `workaround: none` to block; omission is unverified.

1. Before triage, ingest every QA/panel report with `python3 harness/core/scripts/review_calibration.py
   record --run <run> --report <role-report> --milestone <m> --round 1|2
   [--addressed <previous blockers>]`. It reads the active overlay review config; cross-review
   records its own findings. The record CLI also leaves `--round` unspecified by default;
   `--addressed` implies 2 only when no explicit round is supplied. For external context use
   `review_calibration.py record --run <run> --report <role-report> --milestone <m> --round 1
   --addressed <external-report>`; this remains round 1. Distinct report paths/lenses share a round;
   `rounds_used` counts active/completed logical rounds, not lenses or failed attempts. Repeating
   a running/completed review or returning to round 1 after round 2 is refused.
2. Read `<run>/review-ledger.jsonl` and the calibration sidecar, including every verbatim
   malformed/partial finding. Resolve exit-4 triage needs and check every blocker's reachable
   scenario against code (≤5 min each). Verify ratings, workaround and any claimed
   exact overlay invariant. Downgrade edge findings per the matrix, recording the reason.
3. Record each triage decision with `python3 harness/core/scripts/review_calibration.py triage
   --run <run> --id <finding-id> --decision block|backlog --reason "<code-grounded reason>"`.
   Verified corrections may add `--likelihood will|plausible|edge --impact severe|functional|cosmetic
   --workaround "<none or specific workaround>" --invariant "<exact text>" --evidence "<path:line>"`.
   Keep unverified findings Minor; do not fabricate evidence to upgrade them or treat the
   downgrade as approval. Resolve the incomplete blocking claim or identity before dependent work.
4. Fix verified blockers. Minors never block: include fixes costing ≤15 min in the same fix-up;
   send the rest to `<run>/backlog.md`. Re-run checks affected by fixes. Record observed outcomes
   with `review_calibration.py outcome --run <run> --id <id> --outcome failure|harmless|confirmed
   --source fix-up|incident|user-report|review --reason "<observed result>"`;
   add `--user-override block|backlog` only for an explicit user decision, never your own inference.
   `--outcome` is optional when `--user-override` is supplied: an override alone records the
   acceptance decision and leaves an unobserved later outcome unknown; never fabricate an outcome.
5. If needed, run round 2 with `--round 2 --addressed <previous reports>`, the same explicit
   `--milestone <m>` and `--run-dir <run>`. Review only fixes/new code and previous blockers.
   Match source path + reachable caller + failure scenario against prior ledger findings and triage;
   a defect title alone is insufficient, and source/evidence lines may move. An optional trailing
   `— id: <prior stable ledger finding ID>` can reference the same prior finding. Known same-site
   findings with uncertain identity require triage (exit 4); never silently treat them as new backlog.
   A new blocker counts only if severe **and** matrix-blocking; new functional/cosmetic findings go
   to backlog. No automatic third round; unresolved blockers or triage needs go to the gate.

## Taste gates and tripwires

The user steers by results, not by reading agent chats. At a planned gate, and whenever a
tripwire trips (`tripwire.py check` exits 1), stop dispatching work that depends on it (for a
tripwire: the tripped unit) and write `<run-dir>/gates/<gate>.md`:

- **Works now** — what the user can see or run, concretely.
- **Evidence** — the design verifier's screenshot paths for UI, command output, test counts.
- **Deviations** — differences from the spec and decisions taken without the user.
- **Spend** — `python3 harness/core/scripts/tripwire.py report <task>` against the plan.
- **Next** — the following units and what they cost.
- **Questions** — one to three, recommended option first.

Record the card as `task_state.py event <task> gate.open --data '{"gate": "<gate>", "card":
"gates/<gate>.md"}'`, regenerate the run dashboard (`python3 harness/core/scripts/dashboard.py
<task>`, which prints the page's path) and give the user that path with the card: running and
stale lanes, pending gates, tripwires, spend per unit and compare status on one local page.
With herdr, also point to the live `📊 <task>` tab, where the gate shows as pending.
At each taste gate, include `python3 harness/core/scripts/feedback.py list --run-dir <run-dir>
--summary` as one line in the card (Harness feedback below).
Ask through the runtime's question tool and send a push notification when the runtime has one.
Record the answer (continue / redirect with notes / stop) in `run.md` and as `gate.answer`
{gate, answer, source: the user's words} before dependent work.
No answer is not approval: wait. Ready units the gate's question does not touch may continue
when the gate row says it blocks only its dependents. A tripwire card is marked "off course"
and quotes the tripwire lines; after a redirect, record the new scope before the next dispatch.

## QA and panel

QA runs in a fresh context on the tier's profile, once per milestone or PR and after a unit that
changes a contract later units build on (migration, auth mode, config surface): it runs
`gate.py --changed <base> --keep-going` and the boundary checks of
`harness/core/skills/pharos-dev/references/qa-gotchas.md` and of the project's
`harness/project/references/qa-gotchas.md` when present. Panel lenses (per the tier table and
the project roles whose condition holds) run read-only in parallel. A UI-visible change: bring
the application up yourself, then brief the `ui-design-verifier` with its URL, the test sign-in
from the project's seed or fixtures, the spec's screens, states and mockups, and
`<run-dir>/screens/<gate>/`; it captures and assesses the screenshots, and those files are the
gate's and the PR's evidence. Each lens briefs from the spec and the diff, not from a list:
reviewers derive what to look for from the change. Give QA and every lens the overlay's review
stage/invariants, milestone name, round number and previous blockers. All use the calibrated
finding contract and matrix above; ingest their reports with `review_calibration.py record`
for the same milestone/round before triage, so all findings enter `<run>/review-ledger.jsonl`.
Triaged blockers → fix-up and targeted QA; Minor → cheap same-fix-up repair or backlog.
**Ship gate:** no open verified matrix blockers or unresolved triage needs, with at most two rounds per milestone shared
by cross-review, QA and panel. Round 2 checks fixes/new code and previous blockers only; new blockers must
be severe and matrix-blocking. Remaining blockers stop at the gate; a pass never hides failed
required checks. A lens is waived only in the user's words, recorded in `run.md`.

**Architecture.** The `architecture-reviewer` (`critical-review`, read-only) joins the panel on
every Tier 2–3 milestone, not only the PR. When the project has a
`harness/project/architecture.json`, the milestone first runs `python3
harness/core/scripts/arch_check.py --base <milestone base>` — through the project's
`gates.json` when it lists the check, else directly; exit 1 is a fix-up like any failing gate,
exit 2 a broken declaration to fix before the review. The reviewer gets that result, the
overlay's `architecture.md`, the spec's `## Placement` and the milestone diff; a deviation from
Placement is a finding unless the spec was amended.

## G4 and final report — review evidence

At G4 (before the PR) and in the final report, show `python3
harness/core/scripts/review_calibration.py report --run <run>` for the review summary and
outcome counts. Add unresolved triage needs, verbatim malformed/partial claims, triage/downgrade
reasons and remaining blockers from the ledger and fix-up
evidence; outcome counts alone do not prove a blocker was fixed. Repeat `--run <other-run>` for an across-run
report; `--json` emits machine-readable results. Link `<run>/backlog.md` explicitly, including
when empty, and distinguish outcomes not yet observed from `harmless` or `confirmed` ones.

Feedback hook: export each user correction as a sanitized calibration fixture; observed
outcomes can also become regression fixtures. Use
`python3 harness/core/scripts/review_calibration.py fixture --run <run> --id <id>
--case <sanitized-case.json> --out <fixture-path>`. The JSON file contains exactly `name`,
`finding` (one canonical finding string) and `review` (`stage` plus `invariants`). Remove secrets,
personal data and private identifiers from the case; review the generated fixture before
including it in tests/evals.
Use the sanitized fixture and aggregate `report --json` counts to write
`<run>/calibration-proposal.md`, with `What happened`, `Expected`, `Repro (minimal, harness-only)`,
`Evidence` and `Proposal` headings; omit private run paths and raw ledger content. Include a
concrete cell/invariant adjustment, before/after fixture decisions and observed outcome rates.
Create its local reviewable draft with `python3 harness/core/scripts/feedback.py draft
--kind friction --title "Calibrate review acceptance from observed outcomes"
--from-file <run>/calibration-proposal.md --run-dir <run>`. Check the full feedback queue first
for the same underlying issue, including declined/sent drafts. Follow Harness feedback below
for the one-time offer, sanitized preview and explicit user authorization before submission.
The full handoff is documented in `docs/overlay.md`.
Show this hook in G4/final reports so later incidents and user reports update the evidence.

## Handoff

Every "works" claim cites a command run in this session; UI-visible changes ship with the
design verifier's screenshots. At run end, include `python3 harness/core/scripts/feedback.py
list --run-dir <run-dir> --summary` as one line, retaining unsent drafts for the next session.
A PR goes through `submit-pr` when the user asks. Deploys go through CD only.

## Stop and ask

- A tripwire tripped, a gate is reached, a lane needs a material decision or trust/permission
  review, or the same finding survived two
  rounds.
- The implementer flagged that the spec and a frozen test cannot both hold.
- A required capability (second vendor, browser, independent context) is unavailable: record
  the step as not performed; self-review is never relabelled as independent review.

## Harness feedback

Offer feedback when a harness script errors or contradicts its docs; a harness rule forces a
workaround or manual step; a session-built tool belongs in the harness; the user corrects a
profile, tier or gate decision; or the user complains about the harness process. Check the
queue first: `python3 harness/core/scripts/feedback.py list --run-dir <run>`
(or omit `--run-dir` when there is no run). Compare the underlying problem with all
recorded drafts, including declined/sent ones; a new title does not make it a new issue.
Use `list --offerable` only to find existing drafts that still need their first offer.
Draft each distinct issue once with `draft --kind
bug|friction|feature --title "<drafted title>" [--from-file <harness-only evidence>]` using the
same queue selector. At a natural pause after the unit, gate or turn, use `offer <slug>` to
record and ask exactly: **Offer harness feedback “<drafted title>” for your review?** Never
interrupt a running decision. The user decides; reuse their answer. A refusal is `decline
<slug>` in that queue, so it is never offered again; `offer` remembers earlier offers too.

The repository `pharosone/dev-harness` is public. Include only minimal harness reproductions
and sanitized evidence; no product data. `send <slug>` previews the final sanitized text and
checks duplicates before creating anything. Show candidate issue links; only use
`--not-a-duplicate` when the user confirms they are distinct. Publication still needs approval.
The user must see that text before approval;
`--yes` is only for their explicit go for that exact text, never an agent's default. If local
vendored-core edits implement the fix, offer the printed `harness_sync.py propose
--feedback-file <draft>` command instead; it prepares a PR with the reviewed draft attached.
Review its code and body before the user authorizes publishing. `--issue` explicitly chooses
an issue instead. Missing `gh`/auth prints a bounded browser URL; for long drafts, copy
the full sanitized preview into the form. It stays unsent until
publication is verified. At a gate and run end, show `feedback.py list --run-dir <run>
--summary` in one line: **“2 harness feedback drafts waiting: <title>; <title>”**. Declined and
sent drafts remain recorded, without counting as waiting.

## Skill evals

A new skill, or a change to a skill's instructions, ships with at least one added or changed
scenario from its purpose or a recorded failure, with graders for the required behaviour.
Instructions include all files in the skill's directory and its declared references: a
reference-only edit still changes the skill. The content hash covers that complete bundle.
The unit is not done without score with the skill, score without it (baseline), Δ, model,
cost and run date. Require Δ > 0 on every new or changed case and no regression on existing
cases beyond the overlay tolerance (default 0.05). A case whose Δ stays 0 does not test the
skill: fix the case. This covers core and product skills, including non-Claude skills.

Run `python3 harness/core/scripts/skill_evals.py run --skill <SKILL.md path> --model <model>
--run-dir <run> --base <unit base>`; add a cost ceiling with `--max-cost-usd <usd>`.
Evidence goes to `<run>/evals/<skill>.json` and the skill's eval history; quote the scores,
Δ, model, cost and run date in the unit report. Run the cheap gate
`python3 harness/core/scripts/skill_evals.py check --base <unit base>` before accepting the unit.
