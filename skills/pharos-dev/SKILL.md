---
name: pharos-dev
description: "Use when implementing, fixing or refactoring anything in this repository beyond a one-line edit — executing an approved spec, a feature, a bugfix — and on follow-ups (\"сделай\", \"реализуй\", \"исполни спеку\", \"почини\", \"продолжи\", \"перезапусти ревью\", \"redo the QA\"). A review-only request runs the review step alone."
---

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/pharos-dev.md` (this skill's project specifics) before following this
procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it.

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

1. `python3 harness/core/scripts/tripwire.py scope <task> NN --base HEAD [--turn-budget N] <spec globs>`
   in the unit's checkout. Parallel units only when their globs do not overlap, each in
   `python3 harness/core/scripts/worktree.py add <name> [--deps]`; one writer per checkout.
2. Dispatch the writer. The description starts with `[pharos task=<task> unit=NN]` (the
   `PostToolUse(Agent)` hook records spend and runs the tripwires). The brief quotes the unit
   contract from the spec and names the checkout, the paths, the rule files, the run directory
   and the report path. The implementer role defines the dispatch itself (tests → red log →
   `task_state.py freeze --red-log` → code → `verify` → `gate.py`).
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
  lane is `blocked` — show it to the user with its pane and never answer it yourself.
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

## Cross-vendor review — once per milestone

A unit is guarded by its tests, red evidence and tripwires; the other vendor reviews the
**milestone** diff, where sibling units are visible (reviewed one by one, units get their
siblings reported as missing, and each review costs a full pass). Before the milestone's gate:
`python3 harness/core/scripts/xreview.py --base <milestone base> --writer claude|codex --spec <spec>
--sections "<milestone headings>" --ledger <run>/xreview.jsonl --out <run>/reports/<m>-xreview.md`
(the tier comes from `tier.py`; Astra only when the milestone touches Tier-3 paths, otherwise
GPT-6.1 Sol or Claude Opus). Before the PR, review only the diff since the last reviewed
milestone; skip it when nothing changed. The ledger caps critical reviews at five per run.
Exit 0 pass; exit 1 → a fix-up with the Critical/Major findings, then another round with
`--addressed <previous reports>`; keep alternating fix-up and review without asking the user
until the review passes or the cap is used up — only then the gate card (or the user's word
for more). Exit 2/3
(refused, timeout, no verdict) is not approval: record the review as not performed.
A diff over the 200 KB cap is split by area with `--paths` (e.g. the harness paths apart from
the application paths); each part's context lists its siblings so they are not reported missing.

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
reviewers derive what to look for from the change. Reviewers report every issue they find with
a severity; a Critical/Major needs a concrete failure scenario, otherwise it is Minor.
Critical/Major → fix-up and a targeted QA re-pass; Minor → the final report. **Ship gate:** no
open Critical/Major after at most two review iterations. A lens is waived only in the user's
words, recorded in `run.md`.

**Architecture.** The `architecture-reviewer` (`critical-review`, read-only) joins the panel on
every Tier 2–3 milestone, not only the PR. When the project has a
`harness/project/architecture.json`, the milestone first runs `python3
harness/core/scripts/arch_check.py --base <milestone base>` — through the project's
`gates.json` when it lists the check, else directly; exit 1 is a fix-up like any failing gate,
exit 2 a broken declaration to fix before the review. The reviewer gets that result, the
overlay's `architecture.md`, the spec's `## Placement` and the milestone diff; a deviation from
Placement is a finding unless the spec was amended.

## Handoff

Every "works" claim cites a command run in this session; UI-visible changes ship with the
design verifier's screenshots. A PR goes through `submit-pr` when the user asks. Deploys go through CD only.

## Stop and ask

- A tripwire tripped, a gate is reached, a lane is `blocked`, or the same finding survived two
  rounds.
- The implementer flagged that the spec and a frozen test cannot both hold.
- A required capability (second vendor, browser, independent context) is unavailable: record
  the step as not performed; self-review is never relabelled as independent review.

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
