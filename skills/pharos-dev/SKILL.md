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
  "not applicable: <reason>"), TDD units with the paths each may change, and `Taste gates`.
  A missing required section → `pharos-discover` for that gap, reusing answers already given.
  A spec older than taste gates → propose the default gates (first end-to-end slice, each
  milestone, before the PR) with the start question.
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
  `done`). Every report, red log, gate card and spend ledger lives in that directory.

## Tier decides the process

Classify each unit with `python3 harness/core/scripts/tier.py --base <unit base>` after it is
written (and from the spec's paths before); the stricter of the two wins. Which project paths
fall in which tier is set in `harness/project/tiers.json`.

| Tier | Writer profile | Tests | Review | QA | Panel |
|---|---|---|---|---|---|
| 0 docs/demo, ≤10 lines | `routine` or `mechanical` | existing | none | none | none — CI only |
| 1 tests/lockfiles/mechanical, ≤100 lines | `routine` | red→green in the same dispatch when a failing test exists | cross-vendor at the milestone | none | none |
| 2 everything else | `implementation` | red log → freeze → green | cross-vendor at the milestone | one fresh pass per milestone/PR (`routine`) | design review once per PR; UI lens on UI changes |
| 3 auth, tenancy, storage, migrations, config, CI/CD, deploy, harness | `critical-review` | as Tier 2 | cross-vendor at the milestone on `critical-review` (Astra reviews Claude) | `critical-review` | + security and perf lenses; project roles whose condition holds |

**Project roles.** `harness/project/project.json → roles` lists the project's own roles: their
manifest fields and an optional `panel` condition. A role joins the panel as one more read-only
lens when every key of its condition holds: `min_tier` — the unit's tier is at least this;
`paths_exist` — at least one of these paths exists in the checkout. A role without `panel`
works only where an interview row names it (`pharos-discover`).

Refactor is a Tier 2–3 step for the `simplifier` (`critical-review`) when a unit touched more
than three files or a review named duplication, plus one cross-unit pass per milestone;
Tier 0–1 writers tidy their own diff.

## Unit loop

1. `python3 harness/core/scripts/tripwire.py scope <task> NN --base HEAD [--turn-budget N] <spec globs>`
   in the unit's checkout. Parallel units only when their globs do not overlap, each in
   `python3 harness/core/scripts/worktree.py add <name> [--deps]`; one writer per checkout.
2. Dispatch the writer. The description starts with `[pharos task=<task> unit=NN]` (the
   `PostToolUse(Agent)` hook records spend and runs the tripwires). The brief quotes the unit
   contract from the spec and names the checkout, the paths, the rule files, the run directory
   and the report path. The implementer role defines the dispatch itself (tests → red log →
   `task_state.py freeze --red-log` → code → `verify` → `gate.py`).
   Writer choice: Claude by default; GPT-6.1 Sol through herdr for a Tier 1–2 unit whose paths
   no running lane touches. Codex is a worker, never the owner: its brief ends with a report file
   and a ≤ 80-response budget; you run `tripwire.py spend` and `check --record` on its report.
3. Accept on evidence, not on the summary: `tripwire.py check` clean, `task_state.py verify`
   passes, the report quotes gate output from this dispatch. Re-run a gate yourself only when the
   evidence is missing or contradicts the claim.
4. Commit if authorized (explicit paths), `tripwire.py close <task> NN`, mark the row `done`.
   A flag you decided on is archived with `tripwire.py resolve <task> NN "<decision>"`.

## Cross-vendor review — once per milestone

A unit is guarded by its tests, red evidence and tripwires; the other vendor reviews the
**milestone** diff, where sibling units are visible (reviewed one by one, units get their
siblings reported as missing, and each review costs a full pass). Before the milestone's gate:
`python3 harness/core/scripts/xreview.py --base <milestone base> --writer claude|codex --spec <spec>
--sections "<milestone headings>" --ledger <run>/xreview.jsonl --out <run>/reports/<m>-xreview.md`
(the tier comes from `tier.py`; Astra only when the milestone touches Tier-3 paths, otherwise
GPT-6.1 Sol or Claude Opus). Before the PR, review only the diff since the last reviewed
milestone; skip it when nothing changed. The ledger caps critical reviews at four per run —
beyond that, ask the user. Exit 0 pass; exit 1 → a fix-up with the Critical/Major findings,
then one more round with `--addressed <first report>`; still blocked → gate card. Exit 2/3
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

Ask through the runtime's question tool and send a push notification when the runtime has one.
Record the answer (continue / redirect with notes / stop) in `run.md` before dependent work.
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
`<run-dir>/screens/<gate>/`; it captures and judges the screenshots, and those files are the
gate's and the PR's evidence. Each lens briefs from the spec and the diff, not from a list:
reviewers derive what to look for from the change. Reviewers report every issue
they find with a severity; a Critical/Major needs a concrete failure scenario, otherwise it is
Minor. Critical/Major → fix-up and a targeted QA re-pass; Minor → the final report. **Ship
gate:** no open Critical/Major after at most two review iterations. A lens is waived only in
the user's words, recorded in `run.md`.

## Handoff

Every "works" claim cites a command run in this session; UI-visible changes ship with the
design verifier's screenshots. A PR goes through `submit-pr` when the user asks. Deploys go through CD only.

## Stop and ask

- A tripwire tripped, a gate is reached, or the same finding survived two rounds.
- The implementer flagged that the spec and a frozen test cannot both hold.
- A required capability (second vendor, browser, independent context) is unavailable: record
  the step as not performed; self-review is never relabelled as independent review.
