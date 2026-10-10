# dev-harness

Goal-first, risk-tiered agent development for Claude Code and Codex. A run starts from an
approved spec and ends when every "Done when" row is proven by a command run in the session. The
diff's risk tier, computed from its paths and size rather than chosen by the agent, decides how
much testing, review and QA it gets. Between the taste gates where the user steers, deterministic
checks steer: a tool guard, frozen tests, tripwires, and one copy of the CI gates. Every skill,
role and profile exists for both vendors, so the harness runs with either alone.

This file is also the operating manual: skills read it once per workflow, then their runtime's
adapter (`harness/core/adapters/claude.md` or `codex.md`). Paths are relative to the checkout.

## Layout

```
<product repository>
  harness/core/      this repository, a squashed git subtree, never edited in place
  harness/project/   the overlay: project.json, tiers.json, gates.json, drift.json, rules/,
                     roles/, skills/, models.json, architecture.json, architecture.md,
                     HARNESS_LOG.md, tests/
  AGENTS.md          the project's map, written by the project
  CLAUDE.md .claude/ .codex/ .agents/   generated from core + overlay (render_adapters.py)
```

`harness/project` stands for the overlay in use everywhere in the core's skills and roles: `$HARNESS_PROJECT`
when set (relative to the checkout or absolute), else `harness/project`; `python3 harness/core/scripts/project.py overlay`
prints it.

The core has `scripts/`, `skills/`, `roles/`, `adapters/` (runtime notes, `models.json`),
`prompts/`, `plugin/` (skill evals), `tests/` and `examples/project/` (a minimal overlay). Every
overlay file, key and default, and what lands where: [docs/overlay.md](docs/overlay.md).

## Install

`harness_sync.py` ships with the core, so the first install runs it from a clone. The product
checkout needs at least one commit and a clean tree.

```sh
CORE_URL=https://github.com/pharosone/dev-harness.git
git clone --depth 1 --branch v1.0.0 "$CORE_URL" /tmp/dev-harness
mkdir -p harness && cp -R /tmp/dev-harness/examples/project harness/project   # then edit it
python3 /tmp/dev-harness/scripts/harness_sync.py install --repo "$CORE_URL" --ref v1.0.0
python3 harness/core/scripts/render_adapters.py
```

`install` runs `git subtree add --squash` at `harness/core` and pins `core.repo`, `core.ref`,
`core.prefix` in `harness/project/project.json` (not committed). Write `AGENTS.md`, add
`.harness/` (run state and gate logs) to `.gitignore`, review the rendered files and commit them
with explicit paths.

## Update and contribute back

```sh
python3 harness/core/scripts/harness_sync.py status             # pin, latest tag, local edits in the core
python3 harness/core/scripts/harness_sync.py pull [vX.Y.Z]      # default: the latest version tag
python3 harness/core/scripts/harness_sync.py propose --branch fix/<topic> [--title T] [--yes]
```

`pull` merges the release (squashed) in a scratch worktree, moves the pin there, renders the
adapters and runs the core tests and `harness/project/tests`; only when all are green does the
branch fast-forward to that merge and render here. A red check exits 1 and leaves the branch
untouched; a conflict is redone here and left for you to resolve (nothing is committed or
reset). In project CI, `status --strict` fails while `harness/core` differs from
its pin. A local core edit moves into the overlay or goes upstream: `propose` turns the core's
edits since the last sync into a branch of a temporary core clone and prints the push and
`gh pr create` commands; only `--yes`, on the user's request, runs them. The product repository
is never pushed. Core releases follow semver (`CHANGELOG.md`).

## Skills

| User task | Skill |
|---|---|
| Design a feature or fill missing spec decisions (optionally `compare=design`: two approach memos and an arbiter) | `pharos-discover` |
| Find a bug's cause, research data or options for a decision, understand unfamiliar code (optionally `compare=investigation`: two reports, cross-critiques, a disagreement map) | `pharos-investigate` |
| Implement, fix, refactor, or resume approved work | `pharos-dev` |
| Review architecture | `design-review` |
| Create or update a requested PR | `submit-pr` |
| Address a round of PR review comments | `address-gh-review` |
| Write or execute manual verification | `manual-test-plan` |

Skills live in `harness/core/skills/<name>/SKILL.md`. Each skill reads its project specifics
from `harness/project/skills/<name>.md` when present. A project skill with the same name
(`harness/project/skills/<name>/`) replaces the core one. Explaining or inspecting the harness
is not development: do not start a run for it. A harness-only change is tested with the harness
tests, not the application suites.

## Roles

Eight core roles: `implementer`, `simplifier` (writers), `qa-verifier` (runs gates),
`perf-analyst`, `security-auditor`, `architecture-reviewer`, `ui-design-verifier` (read-only;
the browser stays with the last, which takes its own screenshots of the running application).
The `architecture-reviewer` joins the panel on every Tier 2–3 milestone: from the overlay's
`architecture.md` and `architecture.json`, the spec's `## Placement` and the diff, it checks
what `arch_check.py` cannot — the right module, one responsibility per unit, names, no second
implementation, no layer doing another's job. Reviewers build a model of
the change in front of them and choose their techniques from it; project knowledge reaches them
as context. `investigator` (read-only, `deep-reasoning`) does the deep pass of
`pharos-investigate` and of `pharos-discover` in `deep` mode. `harness/project/roles/<role>.md` appends project specifics to a core role; a project
role is declared under `roles` in `harness/project/project.json`, body in `roles/<name>.md`.

## Authorization

Skills cannot grant permissions. Reuse answers and approvals already given and ask only for
missing decisions. An unanswered prompt, a preselected option or an elapsed timeout is not
approval. Hard limits are enforced by `harness/core/scripts/guard.py` on both runtimes, and
repeated here because compaction can drop path rules:
- never deploy from a session;
- stage explicit paths only (`git add <paths>`, never `-A` or `.`);
- push only when the user asks (the guard asks them);
- never read or write `.env` files;
- never touch personal untracked agents and skills;
- never put secrets in briefs, reports or logs.

## Profiles

`harness/core/adapters/models.json`, deep-merged with `harness/project/models.json`, is the only
mapping. Record the resolved model and effort.

| Profile | Claude | Codex | For |
|---|---|---|---|
| `mechanical` | haiku · medium | gpt-6-luna · high | logs, hashes, screenshots, summaries, rote edits a check verifies |
| `routine` | sonnet · medium | gpt-6.1-sol · medium | Tier 0–1 work, UI verification, Tier 1–2 QA |
| `implementation` | opus · medium | gpt-6.1-sol · high | Tier 2 work and fix-ups; cross-review of the other vendor's Tier 1–2 code |
| `critical-review` | opus · high | gpt-6-astra · medium | Tier 3 work, refactor, QA, security, perf, design review, Tier-3 cross-review |
| `escalation` | fable · high | gpt-6-astra · high | after "had the context, tried, still wrong"; science and maths (Astra) |
| `deep-reasoning` | opus · xhigh | gpt-6-astra · xhigh | one deep pass where a wrong answer is expensive: the `investigator` in `deep` mode (a bug's cause, research for a decision, discovery of a hard feature) |

Escalate the model when an agent had the context it needed, clearly tried, and was still wrong.
Raise effort when it skipped steps. `xhigh` is the `deep-reasoning` profile's alone — one pass
whose errors would multiply downstream, chosen by the user in the `deep` mode question; record
its spend so the gain can be measured. `max` nowhere by default.

## Runtime modes

Record the mode in `run.md`. Switch modes when a vendor is unavailable (limit, outage); the run
file lets the other runtime resume.

| Mode | Orchestrator | Writers | Cross-review (`xreview.py`) |
|---|---|---|---|
| both (default) | Claude Code (Opus `high`) | either vendor on the unit's profile, by `balance.py pick` | the other vendor |
| Claude only | Claude Code | Claude | `--reviewer claude`: Claude on `critical-review`, marked single-vendor |
| Codex only | Codex (`gpt-6.1-sol` `high`, never Astra), within the limits in `adapters/codex.md` | Codex | `--reviewer codex`: Codex on `critical-review` (Astra reviews Sol), marked single-vendor |

A single-vendor review is a fresh context on a stronger profile, not an independent vendor
check, and the final report says so. `xreview.py` refuses to switch vendors silently: if the
other CLI is missing, it stops and names `--reviewer`.

In the both-vendors mode the orchestrator picks each writer's and reviewer's vendor by
`balance.py pick --task T --profile P --role R [--not-runtime X]`: the profile fixes the power
(the same profile on both vendors), the pick weighs how fast each vendor's usage windows burn —
a window at `stop_at` excludes a vendor, a projected run-out before the reset steers work away,
each vendor otherwise keeps a minimum share of the run's dispatches. Exit 6 means every allowed
vendor is at its limit: ask the user. Settings: `project.json → balance` (`docs/overlay.md`).

## Delegation

A brief names the role, the unit contract, the checkout, the paths, the rule files, the run
directory and the report path. It never contains secrets. Writers and reviewers get fresh
contexts. Read-only reviewers may write reports and build artifacts, never source, tests, index
or HEAD. One source writer per checkout; parallel writers each get their own worktree
(`harness/core/scripts/worktree.py add <name> [--deps]`). Codex runs as a worker (a lane:
subagent, headless or herdr pane), never as the orchestrator. Without delegation, independent review is **not
performed**, and the report says so.

## Lanes

Every worker starts by `lanes.py decide --role R --runtime claude|codex --orchestrator
claude|codex --minutes N [--needs-user] [--outlives-turn] [--repo PATH] [--tier N]`, which
prints `{"launcher", "reason"}`:

| Launcher | When |
|---|---|
| `pane` (herdr, `HERDR_ENV=1`) | the user will watch or steer (over 20 minutes, a Tier-3 writer, a compare candidate); it needs the user's answers or approvals mid-way; it must outlive the orchestrator's turn; another repository |
| `headless` (`claude -p` / `codex exec`, log in `<run>/lanes/`) | another vendor than the orchestrator, short and report-only; or a `pane` decision without herdr |
| `subagent` | the orchestrator's vendor, short and report-only, its result needed before the next step |

`lanes.py start` launches the decision and records `dispatch.start`/`dispatch.end`; `status`,
`wait`, `result` and `cancel` work the same for every launcher. Herdr holds at most three live
agents, counting ones the run did not start, unless the overlay sets `project.json → lanes`
(`herdr_max_agents`, and `count: "run"` to count only the run's own agents); a `blocked` lane is
shown to the user and never answered by the orchestrator; panes the run did not create are never
closed or reused.

With herdr, `lanes.py dashboard --task T --open` opens the run's dashboard tab `📊 <task>`: on
the left (58%) a live status view (units, lanes running/blocked/stale, pending gates, open
tripwires, vendor shares; every 30 s), the formatted events tail and the overlay's project
panes (`project.json → dashboard`); on the right the run's lane panes, labelled `🤖 <work> ·
<runtime> <model>`. From then on `lanes.py start` puts pane lanes there, reusing a pane whose
lane ended and whose shell is idle before splitting a new one; `--close` closes only the run's
own dashboard panes. Without herdr it is a no-op and `dashboard.py`'s page is the dashboard.

The run directory keeps `events.jsonl`, one JSON event per line (`run.start`,
`dispatch.start`/`dispatch.end`, `gate.open`/`gate.answer`, `compare.*`), appended only through
`task_state.py`'s locked writer: its `event` command, the generated Claude hooks, `lanes.py`
and `compare.py`. `run.md` stays
the narrative; `dashboard.py` renders both, and a run without the log renders from its files
with every missing fact shown as unknown.

## Scripts

Run as `python3 harness/core/scripts/<script>` (standard library only); each module docstring
documents its CLI and overlay keys.

| Script | Use |
|---|---|
| `task_state.py init/locate/freeze/verify/import-legacy` | run directory in the primary checkout (`.harness/tasks/<task>`); freezes tests by Git blob hash, with the red log as evidence |
| `task_state.py event TASK TYPE --data JSON [--id ID]` | the only writer of the run's `events.jsonl`: one locked append, a repeated id skipped |
| `lanes.py decide/start/status/wait/result/cancel/snapshot-cleanup/dashboard` | how a worker starts (subagent, headless CLI, herdr pane) by a tested rule; starts, watches and stops headless and pane lanes and records their events; `start --capability candidate --scratch DIR` runs a compare candidate in a private snapshot with one writable directory, `--max-budget-usd` caps a headless Claude lane, `snapshot-cleanup` removes the ended candidates' snapshots; `dashboard --open/--close` opens or closes the run's herdr dashboard tab |
| `compare.py design/investigation/status` | two independent agents on one frozen input: approach memos and two order-swapped arbiter passes, or investigation reports, cross-critiques and a disagreement map |
| `balance.py usage/observe/pick` | each vendor's usage windows (Claude from captured stream-json logs, Codex from its session files, manual `usage set`) and the pick of a worker's vendor inside one profile; each pick is a `balance.pick` event |
| `dashboard.py TASK [--out PATH]` | the run as one self-contained local page (`<run>/dashboard.html`): lanes, pending gates, tripwires, compares, vendors (usage windows, runway, shares, latest picks), spend per unit |
| `dashboard_herdr.py view status\|events --task T` | the run's herdr dashboard tab behind `lanes.py dashboard`: geometry, lane placement and reuse, the live status and events views it runs in its panes (`project.json → dashboard` adds project panes) |
| `tripwire.py scope/spend/flag/check/report/resolve/close/hook` | unit paths, spend ledger, stop-the-lane facts, decisions on flags; `hook` and `spend --dispatch … --status …` write dispatch events |
| `tier.py --base REF` | risk tier from paths × size (`tiers.json`) |
| `gate.py [AREA…] [--changed BASE] [--dry-run]`, `--pin-ci WORKFLOW` | the one copy of the CI gates (`gates.json`), quiet output, full logs on disk; the pin against CI |
| `quiet.py -- CMD` | run any command, print its failures and tail, keep its exit code |
| `xreview.py` | cross-vendor review of a milestone diff, report only; withholds secrets, the Codex reviewer carries the guard, `--ledger` caps critical reviews (5 per run) |
| `worktree.py add/remove` | lane worktrees; `--deps` installs `worktree.node_dirs` |
| `guard.py` | PreToolUse guard for both runtimes (generated hooks; `project.json → guard`) |
| `check_docs_drift.py --base REF` | docs and contracts follow structural changes (`drift.json`; CI too) |
| `arch_check.py [--base REF \| --all]` | the declared architecture holds (`architecture.json`): layer dependencies, files outside every layer, misplaced kinds, names; off without the file; exit 0/1/2 |
| `runtime_hook.py` | the Stop hook adapter for the drift check |
| `render_adapters.py [--check] [--root REPO]` | render the native adapters from core + overlay |
| `harness_sync.py install/status/pull/propose` | vendor, check, update and upstream the core |
| `project.py` | where the core and the overlay live; how scripts read the overlay |
| `skill_evals.py check/run/report` | paired core/product skill measurements, tracked history and the cheap freshness/acceptance gate |

Claude Code loads the rules in `harness/project/rules/*.md` by path; pass their paths to agents
of other runtimes. Run evidence must let another runtime resume without a living session.

## Validation

```sh
# a product repository
python3 harness/core/scripts/render_adapters.py --check
python3 -m unittest discover -s harness/core/tests -p 'test_*.py'
python3 harness/core/scripts/check_docs_drift.py --base origin/main
python3 harness/core/scripts/harness_sync.py status --strict
# this repository (CI also renders examples/project in a scratch repository and checks it)
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/skill_evals.py run --skill pharos-dev --model sonnet --max-cost-usd 2 --run-dir .harness/skill-evals
python3 scripts/skill_evals.py run --skill pharos-discover --model sonnet --max-cost-usd 2 --run-dir .harness/skill-evals
python3 scripts/skill_evals.py check --base origin/main
python3 scripts/skill_evals.py report
```

Run the affected skill suites after editing instructions and on a model release. Each case
comes from the skill's purpose or a recorded failure; store paired measured evidence and run
the cheap gate before acceptance (see [Skill evals](#skill-evals)).
After an overlay change run `render_adapters.py` and commit the reviewed output; every harness
change gets a row in `harness/project/HARNESS_LOG.md` (what, where, the failure that caused it).

## Known limits

The checks are guardrails, not proofs. Their known gaps are deliberate, and are listed so that a
reviewer does not mistake them for coverage:
- `guard.py` reads common shell forms. A determined agent can still write a file through an
  interpreter or a pipe into a shell. Branch protection and CD reviewers are the real gate for
  push and deploy.
- The CI pin checks that each gate command runs, not its arguments, so a test command with a
  filter that selects nothing would satisfy it. CI changes are Tier 3 and reviewed.
- `tier.py` classifies by path and size, not meaning; `check_docs_drift.py` checks that a mapped
  document changed, not that it is right.
- Checks that live in test files are frozen together with their cases: `task_state.py verify`
  flags a fix to such a check as a test change, and the orchestrator records why it holds.
- Codex loads a project's `.codex/` hooks only on a trusted path, and every worktree is a new
  path. Workers carry the guard on the command line (`adapters/codex.md`).

## License

Apache-2.0. See [LICENSE](LICENSE).

## Skill evals

A new skill, or a change to a skill's instructions, ships with at least one added or changed
eval case: a scenario from its purpose or a recorded failure, with graders for required
behaviour. The unit is not done without score with the skill, score without it (baseline),
Δ, model, cost and run date. Require Δ > 0 on every new or changed case and no regression on
existing cases beyond the overlay tolerance (default 0.05). A case whose Δ stays 0 does not
test the skill: fix it. This applies to harness and product skills, including Hermes.

`skill_evals.py run --skill <name or SKILL.md path> --model <model> --max-cost-usd <usd>
--run-dir <run> --base <unit base>` writes `<run>/evals/<skill>.json` and a timestamped,
committed history under `<eval dir>/history/<skill name>-<path hash>/`. The unit report
quotes the paired scores, Δ, model, cost and run date. `report` shows latest evidence and
flags stale skill or case hashes. Case ownership is explicit: `<eval dir>/cases.json` maps
checkout-relative `SKILL.md` paths to lists of case-directory names; the core registry uses
core-relative paths. Each harness case has `prompt.md` and `graders/*.md`. Keep history in
Git so the next change can compare existing-case scores to the previous accepted evidence.
Missing historical scores are reported as a coverage gap; they are not invented.

`check --base REF` compares REF to committed, staged, unstaged and untracked files. For each
added/changed skill it requires an added/changed registered case and evidence matching the
current SHA-256 of both skill instructions and every case file. It validates new-case Δ
and existing-case regressions against the latest accepted history at REF. It makes no model calls.
Case-only edits also require refreshed evidence. Deleted skills need no eval. Registry-only edits do not count as a new scenario or grader.
Exit codes: 0 clean, 1 unmet acceptance, 2 configuration/invalid evidence. Review histories
as evidence: the gate checks the writer's recorded measurements, not their authenticity.

Product `project.json` may declare `skills` (see docs/overlay.md). Add this area to the
product's `gates.json` and the identical command to CI:

```json
"skill_evals": {"paths": ["**"], "steps": [{"name": "skill-evals", "cwd": ".",
  "cmd": ["python3", "harness/core/scripts/skill_evals.py", "check", "--base", "{base}"]}]}
```

Harness evals wrap `claude plugin eval` with `--ablation with-without` in an isolated plugin
containing only the selected skill and its registered cases. Reports stay local
(`--no-publish`); no real servers or author scaffold scripts run. Product runners receive
`--skill <checkout-relative SKILL.md> --out <JSON path>` plus requested `--model` and
`--max-cost-usd`. The command is split into argv, never interpreted as shell code. It must
run each registered case with and without the skill and write:

```json
{"model": "actual-model", "cost_usd": 0.12, "partial": false,
 "cases": [{"name": "voice-tone", "score_with": 1.0, "score_without": 0.4}]}
```

Scores are finite 0..1; every registered case must appear exactly once. The wrapper computes
Δ, adds hashes/date, saves evidence and checks acceptance. Runner failure, missing arms,
partial output, changed input during the run or an exceeded ceiling cannot create fresh
evidence. A cost ceiling is forwarded to the runner; Claude checks between launches, so a
single in-flight run may overshoot and its evidence is rejected.

Codex-only equivalent: configure `skills.eval_command` as a paired Codex runner (or the
product's Hermes runner), register its cases and use `run --runtime codex` plus the same
`check` and `report` commands. `--runtime codex` also routes core skills through the overlay
runner; their registered cases remain the core's cases. The runner should use fresh `codex exec` contexts for the with-skill and
no-skill arms and apply the same graders. The default built-in harness runner requires Claude;
`--runtime codex` explicitly selects the configured paired runner and never silently substitutes
a different vendor. In Claude-only mode use the built-in
runner unchanged. On a model release, rerun the affected suites and record the model.
