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
git clone --depth 1 --branch v1.4.0 "$CORE_URL" /tmp/dev-harness
mkdir -p harness && cp -R /tmp/dev-harness/examples/project harness/project   # then edit it
python3 /tmp/dev-harness/scripts/harness_sync.py install --repo "$CORE_URL" --ref v1.4.0
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
- never deploy from a session; an owner may list vetted workflow files a Claude
  orchestrator may dispatch in `guard.allow_workflows`;
- stage explicit paths only (`git add <paths>`, never `-A` or `.`);
- by default, Claude asks for each push; an owner may authorize autonomous ordinary feature
  pushes with `project.json` → `guard.push: "allow"`. Codex lanes always remain denied.
  Force/lease, mirror, deletions, main/master or cached remote HEAD, hidden verbs and
  ambiguous/compound pushes still ask. Changing the policy itself asks once. See
  [guard configuration](docs/overlay.md#guard);
- never read or write `.env` files, except tracked, secret-free ones the overlay names in
  `guard.env_exempt` ([guard configuration](docs/overlay.md#guard));
- never touch personal untracked agents and skills;
- never put secrets in briefs, reports or logs.

## Profiles

`harness/core/adapters/models.json`, deep-merged with `harness/project/models.json`, is the only
mapping. Resolve every orchestrator and worker model/effort from these harness profiles at
dispatch time; never use personal memory or a saved model preference. Record the resolved
profile, model and effort. The router's shadow recommendation is advisory evidence and never
replaces this actual profile choice.

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
its spend so the gain can be measured. `max` nowhere in the static dispatch defaults. The
optional shadow router separately examines all supported efforts inside measured quality
bands; it does not change these defaults.

## Benchmark evidence

Benchmark research for the proposed task router is available in
[docs/benchmarks](docs/benchmarks/README.md), with provenance in
[data/benchmarks/SOURCES.md](data/benchmarks/SOURCES.md). It covers the latest retained classes
of Claude, GPT/Codex, Gemini and Grok, including Arena categories, settings and uncertainty.
Research coverage includes BrowseComp variants, DRACO, WANDR and SimpleQA-Verified, with
explicit gaps where current model/scaffold evidence is absent. The versioned task taxonomy
includes `web_research` for source-backed research and cited fact extraction.
[Per-model effort curves](docs/benchmarks/effort-curves.md) show quality, token use and
derived decode time, with unsupported or unmeasured settings kept explicit. These are
benchmark resource measurements and estimates, not subscription quota or task wall time.
Gemini and Grok runtime support is marked **planned**. The optional shadow router uses this
snapshot for advisory recommendations; actual profile mapping, vendor selection and lane
launch behavior remain unchanged. The classifier is **UNVALIDATED**; its accuracy experiment
and the lead-research pilot have not run.

From this core checkout, validate with `python3 scripts/benchmarks.py validate` and regenerate
the offline charts with `python3 scripts/benchmarks.py render`. Rendering does not fetch new
results; refresh the sourced JSON first. Observations older than 60 days produce warnings.

## Shadow model routing — 1.5.0

Set `harness/project/router.json` to `{"mode":"shadow"}` to add recommendations to
`balance.py pick` and `lanes.py decide/start`. Missing configuration or `mode: "off"` retains
1.4.0 behavior. Actual dispatch always uses the existing static pick or explicit launch tuple.
Both dashboards show `⚖ shadow: would pick … because …` separately from that actual choice.

The [registry and pure scorer](docs/benchmarks/router-scoring.md) enumerate every supported
effort, apply AA v4.3.2 reference bands and task-quality floors, and price quota scarcity plus
full expected rework. Exact independent cohorts are required. Unknown input/quota/wall-time
costs remain unscored unless explicit bounded estimates or labeled AA proxy scenarios are
configured. Gemini/Grok remain planned. No active-routing mode is implemented.

Provide manual trait weights or explicitly enable the subscription Haiku 5.5 classifier;
classification is off by default, tool-free, cached by task/prompt/model/effort and marked
UNVALIDATED. Failure or timeout records a skip. [Configuration, commands and input
contract](docs/router-shadow.md) describe runtime restrictions and cost assumptions.

[Calibration hooks](docs/benchmarks/router-calibration.md) link decisions, real dispatches,
explicit acceptance/fixups and the 1.4.0 review ledger. Process completion is never acceptance;
requested aliases and effort are separate from observed serving identity. The report exposes
coverage and unknowns; it does not fit probabilities or quota prices from incomplete evidence.

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

Use this brief contract for every lane: `Final report: <report>`;
`Progress: <report>.progress.md`; `Needs coordinator input: <run>/lanes/<work>.flag.md`.
Write the final report only when work ends. Put status updates in the progress file. An
unresolved question goes into the flag (or `tripwire.py flag`); never type into another
lane's or the coordinator's pane. The coordinator reads flags in the same turn as the
watcher's `LANE <work> needs-input flag` alert and removes a flag after resolving it.
An open unit tripwire flag also surfaces; resolve it with `tripwire.py resolve`. A worker
whose sandbox cannot write the run directory uses `<report>.flag.md`, written after dispatch
start. Native subagent dispatch events use the same watched channel. Flag text stays out of events.

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
(`herdr_max_agents`, and `count: "run"` to count only the run's own agents); a `blocked` lane's
dialog must be read: answer questions you can decide from the spec and earlier authorizations;
never answer trust/permission reviews; escalate material decisions to the user. Panes the run did not create are never
closed or reused.

Before starting an agent, lanes waits up to 15 seconds for the shell's foreground group to
contain only the shell. An unavailable-shell refusal retries once; a shell that never becomes
ready fails the launch. Reused terminals receive raw Ctrl+C, kitty keyboard reset and
`stty sane`; a fresh prompt marker must appear within 5 seconds before checkout/environment
commands are typed. A failed reset skips that pane.

With herdr, `lanes.py dashboard --task T --open` opens the run's dashboard tab `📊 <task>`: on
the left (58%) a live status view (units, lanes running/blocked/stale, pending gates, open
tripwires, vendor shares; every 30 s), the formatted events tail and the overlay's project
panes (`project.json → dashboard`); on the right the run's lane panes, labelled `🤖 <work> ·
<runtime> <model>`. From then on `lanes.py start` puts pane lanes there, reusing a pane whose
lane ended and whose shell is idle before splitting a new one; `--close` closes only the run's
own dashboard panes. Without herdr it is a no-op and `dashboard.py`'s page is the dashboard.

At run start arm `python3 harness/core/scripts/watch.py --task T`: Claude Code uses `Monitor`
with an unbounded command and the tool's maximum timeout, re-arming on expiry or unexpected exit; Codex starts a
background process and reads its output each turn and before going idle, or runs `--once` at
those boundaries. Polling defaults to 30 seconds, repeat suppression to 600 seconds
(`--every`, `--quiet-repeat`); cleared conditions re-arm immediately. The watcher emits
`LANE <work> needs-input <what>`, `frozen <minutes>` (working agent with both unchanged screen
and no checkout file activity for 15 minutes), `ended-no-report`, `exited` or `done`, and `CHECK <name> <anomaly>` / `failed` from the
overlay's `watch.checks`. Without herdr, lane checks use dispatch state and product checks
still run. State lives in `<run>/lanes/_watch.json`; `watch` events appear in both dashboards,
with `Watch: ✅ quiet` / `Watch: 🚨 N open` in the status view.
An idle agent is not frozen. A progress or final report written while the agent is working
does not mean done: require a recorded dispatch end, or an idle/done agent with a fresh final
report. Lane flags surface as `needs-input flag`. Codex hook reviews surface as
`needs-input hook-trust`; read the dialog and press `t` only after the user authorizes trust.
Checkout activity includes tracked and untracked-not-ignored files, plus report/progress stamps,
excluding `.git` and observer-owned `.harness`. Listing/stat work is bounded to 5 seconds and
50,000 paths; unreadable or over-budget activity cannot prove a freeze. Watch reports
`LANE <work> activity-unknown (<why>; frozen cannot be detected)` once per dispatch.
Ignored dependency/build trees do not count as activity. The working-indicator line's
spinner and elapsed timer are normalized; changes to tool output or token counts still count.
Failed screen/state
reads clear freeze evidence and restart its clock once fresh observations return. Unanswered
dialogs remain visible. A vanished agent/dead supervisor remains `exited` until dispatch end.
The hook alert names the one-key action: `the user presses t (Trust all and continue) in <pane>`.
Codex's available bypass trusts all enabled hooks; the harness uses this narrow watch fallback
instead of bypassing checkout hooks or editing undocumented global trust hashes.

Act on every line in the same turn: answer or escalate input, nudge or cancel frozen lanes,
inspect missing reports/exits, accept done reports, and observe waiting heavy jobs. A queued
heavy job is not a frozen lane; after admission its inactivity evidence restarts. Stop the product's live process on a
repeating product failure through its own `watch.stop_command` before another retry. If the
stop command is absent, escalate for the product's stop procedure. Tell the user in 2–3 lines
what was done; silence is fine when nothing fires. The script only observes and records;
the orchestrator acts. Stop the watcher when the run ends. Configuration:
[watch in the overlay](docs/overlay.md#watch--lane-and-product-observations).

After accepting a report or handling an ended-no-report dispatch, run
`python3 harness/core/scripts/watch.py --task T --ack D` as part of that acceptance/recovery.
This permanently closes the dispatch's terminal watch alert; new work gets a new dispatch id.
The command cannot acknowledge live input/freeze alerts. Runtime poll errors are logged as
`watch.error` events and retried; bad startup configuration exits 2. `--once` returns 1 on a
poll failure. Pane starts keep a registration window open while their recorded `launch_pid`
is alive and `launch_pending` is true; successful startup closes it immediately, and a dead
launcher can be reported as exited. Legacy states without a launcher PID have five minutes
of grace. Health commands may emit JSON lines with stable
`key` and changing `message` fields to suppress changing counters. HTML shows the latest
50 watch events, with the full history kept in `events.jsonl`.

The run directory keeps `events.jsonl`, one JSON event per line (`run.start`,
`dispatch.start`/`dispatch.end`, `gate.open`/`gate.answer`, `compare.*`, `watch`), appended only through
`task_state.py`'s locked writer: its `event` command, the generated Claude hooks, `lanes.py`
and `compare.py`. `run.md` stays
the narrative; `dashboard.py` renders both, and a run without the log renders from its files
with every missing fact shown as unknown.

## Feedback

Users and their agents improve the public harness through reviewed issues or PRs. Offer once
per distinct issue at a natural pause (unit, gate or turn end): a script error or contradiction
of its docs; a rule requiring a workaround/manual step; a session tool the harness should own;
a user correction to a profile/tier/gate choice; or a complaint about the harness process.
Never interrupt a running decision. Ask exactly: **Offer harness feedback “<drafted title>”
for your review?** The user decides; a declined offer is recorded and never repeated.

```sh
python3 harness/core/scripts/feedback.py draft --kind bug --title "<title>" --from-file <harness-only.md> --task <task>
python3 harness/core/scripts/feedback.py list --task <task>
python3 harness/core/scripts/feedback.py offer <slug> --task <task>
python3 harness/core/scripts/feedback.py decline <slug> --task <task>
python3 harness/core/scripts/feedback.py send <slug> --task <task>
```

Drafts live in `<run>/feedback/<slug>.md`. Use `--task` for the primary checkout's durable run,
`--run-dir` for an existing run directory (including an overlay-owned one), or omit both for
`<checkout>/.harness/feedback`. A repeated title retains the existing draft and its state.
Check the full list before drafting: compare the problem with declined/sent drafts even if its wording changed.
Use `list --offerable` only to find existing drafts awaiting their first offer.
`list` shows draft, declined, or sent with its URL; `offer` records the one-time question.
At each taste gate and run end, `pharos-dev` shows `list --summary` using the same queue:
“2 harness feedback drafts waiting: <title>; <title>”.

`send` sanitizes both title and body, shows the exact final text, checks possible duplicates
with `gh issue list --search` (open and closed), and creates an issue in the `core.repo` pin,
which must resolve to `pharosone/dev-harness`. Possible duplicates are shown with issue links
and block creation for review. If the user confirms they are distinct, rerun with
`--not-a-duplicate`; publication still needs approval. Approval is for the displayed text;
agents use `--yes` only after the user's explicit go. Without `gh` or authentication, an
`issues/new` URL of at most 2,000 characters is printed and the draft stays unsent. If the encoded body would exceed that limit, copy the full sanitized preview into
the form; an oversized title must also be copied. A send error retains the draft; check
upstream before retrying an ambiguous failure.

With local edits in the vendored core, `send` offers `harness_sync.py propose --feedback-file
<draft>` instead of creating an issue. That option attaches a link and the reviewed draft to
the PR body and sanitizes its title/body; preparation publishes nothing. Review the diff as
well as the body before authorizing publication: the feedback sanitizer does not scrub code.
Successful publication through `propose --feedback-file --yes` records the PR URL and marks
the draft sent. Declined or sent attachments are refused. Use `send --issue` to explicitly
choose an issue instead; this works even in a copied or shallow core without subtree history.

**No product data:** use a synthetic, minimal harness reproduction. The mandatory sanitizer
removes product/overlay names case-insensitively as whole words or token parts delimited
by `-`, `_`, `.`, `/` or digits, while preserving ordinary words such as `application`
for a name of `app`. It also scrubs absolute paths outside the core (including redirects
and HTML), emails, IPs, known keys/tokens,
and extra literal terms in `project.json → feedback.redact` ([overlay](docs/overlay.md#feedback)).
Names shared with harness scripts or commands preserve those harness terms. Explicit private
terms remain literal redactions. Sanitized placeholders and Markdown link punctuation survive.
Only HTTPS links to this core or the explicit public documentation allowlist survive; query
strings/fragments are removed. Current docs allowlist: `docs.python.org`, `docs.github.com`,
`learn.chatgpt.com/docs`, `platform.openai.com/docs`, `code.claude.com/docs`,
`docs.anthropic.com`, `herdr.dev`. Remaining known secret patterns refuse sending. Read the
final text: arbitrary business facts are not recognizable by pattern matching. Drafts are
local review material, never automatically published.

## Frozen test amendments

Freeze after the first red run with `task_state.py freeze TASK UNIT --red-log FILE TESTS…`.
After the first passing test command, immediately record
`task_state.py green TASK UNIT --green-log FILE`; it verifies the frozen hashes and pins
the first green evidence. A hash-only `verify` does not record test success.

Repeating `freeze` automatically archives the previous manifest and logs its reason only for
Python AST-equivalent edits (comments, whitespace and removed provably unused imports), or setup-only
repairs before recorded first green with byte-identical assertion lines and hash-verified
setup-error evidence whose innermost traceback identifies the exact frozen setup function.
Only pure, unobserved local bindings qualify: literal/name assignments with no outward use,
calls, properties or operators. Returned/yielded fixture values, attributes, globals, mutable
shared data and `Fake*`/`Stub*` helpers remain frozen because they can supply assertion inputs.
An unrelated import error or evidence without a local traceback never authorizes a setup edit.
Assertions, test bodies, parameters and decorators stay protected;
inline construction inside a test body remains approval-only. Mixed assertion/setup failures
do not qualify. Legacy manifests without saved source bytes remain approval-only.
Unknown imported bindings can be collected tests/fixtures: removing them needs approval;
unused import cleanup is limited to known non-collection standard-library modules.
Setup repairs may add a missing non-collecting stdlib import used only by an implicated pure
local setup binding, but cannot remove/retarget existing imports or overwrite definitions/bindings.
Setup/fake bodies with skip/xfail controls retain their full AST, including guards and aliases.
First-green evidence must show actual passing tests; bare unittest `OK`, empty suites and
all-skipped/expected-failure-only runs are refused.

A brief can authorize literal additions before the initial freeze: pass `--brief BRIEF`,
whose exact line is `task-state append-only: tests/test_tools.py:EXPECTED` (a scoped name
such as `TestTools.test_tools.expected` also works). Only append literal elements to that
named list, tuple or set assignment; existing elements/order and all other AST nodes must
stay identical. Authorization is pinned at initial freeze and cannot be added retrospectively.
Every other change stops for coordinator approval; preserve the old manifest and record the
approval before replacing that baseline. Refreeze never offers a general bypass flag.

## Scripts

Run as `python3 harness/core/scripts/<script>` (standard library only); each module docstring
documents its CLI and overlay keys.

| Script | Use |
|---|---|
| `task_state.py init/locate/freeze/green/verify/import-legacy` | run directory in the primary checkout (`.harness/tasks/<task>`); freezes tests by Git blob hash, archives safe amendments, records first green evidence |
| `task_state.py event TASK TYPE --data JSON [--id ID]` | the only writer of the run's `events.jsonl`: one locked append, a repeated id skipped |
| `lanes.py decide/start/status/wait/result/cancel/snapshot-cleanup/dashboard` | how a worker starts (subagent, headless CLI, herdr pane) by a tested rule; starts, watches and stops headless and pane lanes and records their events; `start --capability candidate --scratch DIR` runs a compare candidate in a private snapshot with one writable directory, `--max-budget-usd` caps a headless Claude lane, `snapshot-cleanup` removes the ended candidates' snapshots; `dashboard --open/--close` opens or closes the run's herdr dashboard tab |
| `compare.py design/investigation/status` | two independent agents on one frozen input: approach memos and two order-swapped arbiter passes, or investigation reports, cross-critiques and a disagreement map |
| `balance.py usage/observe/pick` | each vendor's usage windows (Claude from captured stream-json logs, Codex from its session files, manual `usage set`) and the pick of a worker's vendor inside one profile; each pick is a `balance.pick` event |
| `dashboard.py TASK [--out PATH]` | the run as one self-contained local page (`<run>/dashboard.html`): lanes, pending gates, tripwires, compares, vendors (usage windows, runway, shares, latest picks), spend per unit |
| `dashboard_herdr.py view status\|events --task T` | the run's herdr dashboard tab behind `lanes.py dashboard`: geometry, lane placement and reuse, the live status and events views it runs in its panes (`project.json → dashboard` adds project panes) |
| `watch.py --task T [--once] [--every S] [--quiet-repeat S] [--ack D]` | observe lane dialogs, frozen screens, reports and exits, plus overlay product checks; suppress repeats, append `watch` events and acknowledge terminal lane acceptance/recovery |
| `tripwire.py scope/spend/flag/check/report/resolve/close/hook` | unit paths, spend ledger, stop-the-lane facts, decisions on flags; `hook` and `spend --dispatch … --status …` write dispatch events |
| `tier.py --base REF` | risk tier from paths × size (`tiers.json`) |
| `gate.py [AREA…] [--changed BASE] [--dry-run]`, `--pin-ci WORKFLOW` | the one copy of the CI gates (`gates.json`), quiet output, full logs on disk; the pin against CI |
| `quiet.py -- CMD` | run any command, print its failures and tail, keep its exit code |
| `review_calibration.py` | record and triage findings, link outcomes, report per-run/across-run calibration, export sanitized correction fixtures |
| `xreview.py` | cross-vendor review of a milestone diff, report only; withholds secrets, the Codex reviewer carries the guard, `--ledger` caps critical reviews (5 per run) |
| `worktree.py add/remove` | lane worktrees; `--deps` installs `worktree.node_dirs` |
| `guard.py` | PreToolUse guard for both runtimes (generated hooks; `project.json → guard`) |
| `check_docs_drift.py --base REF` | docs and contracts follow structural changes (`drift.json`; CI too) |
| `arch_check.py [--base REF \| --all]` | the declared architecture holds (`architecture.json`): layer dependencies, files outside every layer, misplaced kinds, names; off without the file; exit 0/1/2 |
| `runtime_hook.py` | the Stop hook adapter for the drift check |
| `render_adapters.py [--check] [--root REPO]` | render the native adapters from core + overlay |
| `feedback.py draft/list/offer/decline/send` | sanitized public harness feedback queue, one-time offers, duplicate check and reviewed issue/PR handoff |
| `harness_sync.py install/status/pull/propose` | vendor, check, update and upstream the core |
| `project.py` | where the core and the overlay live; how scripts read the overlay |
| `skill_evals.py check/run/report` | paired core/product skill measurements, tracked history and the cheap freshness/acceptance gate |

Claude Code loads the rules in `harness/project/rules/*.md` by path; pass their paths to agents
of other runtimes. Run evidence must let another runtime resume without a living session.

## Efficient testing

Read [the core testing rule](rules/efficient-testing.md) before adding test infrastructure.
It applies to every project/stack and both runtimes: use in-process fakes when sufficient,
otherwise shared long-lived test services with owned logical namespaces, then per-run
services, and per-test processes/containers only for a written correctness reason. Reuse
migrations/fixtures/assets/model downloads under complete content-hash keys; keep build,
result, dependency and image caches on. A cache exception names the invisible input and the
one affected case/step. Locally run affected tests first; the full suite stays in CI and the
pre-PR gate. Bound child fan-out by the host budget and report cold/warm wall time and
resource/container cost when infrastructure changes.

Use [the implementer brief template](prompts/implementer-brief.md), including `## Goal` and
its Test infrastructure section. QA checks the isolation choice, cleanup and cache exceptions
against the same rule. Heavy admission is adaptive host telemetry rather than fixed slots;
waiting and gate timing are advisory evidence, never a replacement for correct test results.
Admission is implemented by `harness/core/scripts/load_guard.py`. Configuration belongs in [the overlay](docs/overlay.md#load_guard--adaptive-host-admission).

`gate.py` recognizes full test suites vs file/package/filter selections; an opaque wrapper
can declare `scope: "full"` or `scope: "targeted"` in its step, and `--targeted` makes an
explicit affected-only gate bypass admission. `--changed` selects areas, not test cases:
if a selected area's command still runs its entire suite, it remains a full command.
Lint/format/type/doc checks, dry-run and CI pin checks never wait. `--run-dir <run>` writes
`gate-timings.jsonl`; successful comparable steps maintain a bounded rolling history in the
primary checkout, with >50% slowdown advisory and guard wait separated from execution.

## Validation

```sh
# a product repository
python3 harness/core/scripts/render_adapters.py --check
python3 -m unittest discover -s harness/core/tests -p 'test_*.py'
python3 harness/core/scripts/check_docs_drift.py --base origin/main
python3 harness/core/scripts/harness_sync.py status --strict
# this repository (CI also renders examples/project in a scratch repository and checks it)
python3 -m unittest discover -s tests -p 'test_*.py'
python3 scripts/skill_evals.py run --skill pharos-dev --model sonnet --max-cost-usd 2 --run-dir .harness/skill-evals --base origin/main
python3 scripts/skill_evals.py run --skill pharos-discover --model sonnet --max-cost-usd 2 --run-dir .harness/skill-evals --base origin/main
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
- Checks that live in test files are frozen together with their cases: logic changes still
  need coordinator approval; only the verified amendment categories above refreeze automatically.
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
current SHA-256 of the whole skill directory, declared references and every case file. It validates new-case Δ
and existing-case regressions against the latest accepted history at REF. It makes no model calls.
Case-only edits also require refreshed evidence. Reference-only changes also require a changed case and fresh evidence for each dependent skill. Deleted skills need no eval. Registry-only edits do not count as a new scenario or grader.
External Markdown references normalize only standalone `Version: X.Y.Z` and
`Date: YYYY-MM-DD` lines, and release headings in `CHANGELOG.md` (`## X.Y.Z`, optionally
` — YYYY-MM-DD`, ` - YYYY-MM-DD`, or ` — Unreleased`). Fenced examples, inline versions,
release bodies and all files inside the skill directory keep their exact bytes. A change
whose normalized reference bytes equal the base is non-invalidating; other changes still
need fresh paired evidence. Old raw-hash history remains accepted for metadata-only bumps.
Exit codes: 0 clean, 1 unmet acceptance, 2 configuration/invalid evidence. Review histories
as evidence: the gate checks the writer's recorded measurements, not their authenticity.

Product `project.json` may declare `skills` (see docs/overlay.md). Add this area to the
product's `gates.json` and the identical command to CI:

```json
"skill_evals": {"paths": ["**"], "steps": [{"name": "skill-evals", "cwd": ".",
  "cmd": ["python3", "harness/core/scripts/skill_evals.py", "check", "--base", "{base}"]}]}
```

Harness evals wrap `claude plugin eval` with `--ablation with-without` in an isolated plugin
containing the selected skill, its registered cases and its validated declared input bundle,
copied at the `harness/core` and `harness/project`
paths so the measured reference files are present in the isolated eval bundle. Runtime
permission restrictions still apply to reads. Reports stay local
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

The skill content hash covers every directory file (assets and scripts too), symlink targets,
and transitive local references declared as literal file paths or Markdown links in `.md`/`.txt`
inputs. `harness/core/` paths resolve against the actual core; `harness/project/` paths use the
active overlay. Both runtime adapters are inputs when a core skill declares the active adapter.
Declare dynamically selected files or script imports in `<skill dir>/eval-inputs.json`, a JSON
list of skill-relative paths/globs, for example `["../../shared/*.md"]`. Directories recurse.
The manifest itself is hashed. Paths must stay in the checkout; `.env` inputs are refused.
Only `.git` metadata and `__pycache__` are excluded. Deleted references remain hash inputs;
addition/deletion and changes during a run invalidate evidence. Schema 2 records `input_hashes`
alongside the composite `content_hash`; legacy SKILL.md-only evidence is stale and must rerun.
Product overlays therefore affect freshness when a skill declares them; an evaluation in the
bare core does not claim evidence for a different overlay. `run` requires an explicit `--base`.

Changed case content requires positive Δ; score tolerance compares identical case content.
An initial/new case cannot acquire an accepted history with Δ ≤ 0 even after it was committed.
External runners also receive `HARNESS_SKILL_EVAL_MANIFEST` in their environment: the path to
a JSON manifest containing `skill`, `content_hash`, the registered `cases` (`name` and absolute
`path`), and `input_files` (canonical labels to absolute paths). Use these case paths directly
instead of duplicating registry/core-vendoring resolution. Existing runner argv stays compatible.

## Calibrated review acceptance

Review likelihood and impact against this product's normal paths. `xreview.py` recomputes
Critical/Major blockers from the approved matrix. Missing fields or code-read evidence remain
Minor until verified; they cannot approve a reviewer's blocking claim. Exit **1** means a
computed blocker, **4** means needs triage, **0** alone approves; **2/3** mean refused/failed,
timeout or missing verdict, never approval. Fully verified matrix backlogs can override a
reviewer's block to pass. Backticks/bold and line ranges are normalized; explicit malformed
or partial findings remain verbatim in `<report>.calibration.md` and `<run>/review-ledger.jsonl`.
Configure `project.json.review` with `stage` (default `pilot`) and short product `invariants`
(default empty). See [review policy and calibration loop](docs/overlay.md#review--calibrated-acceptance-projectjson).

Use repeated `--section 'Limits, retries'` for headings containing commas; legacy `--sections`
still works. Persisted xreview calls (`--run-dir` or `--ledger`) require explicit `--milestone M1`;
standalone reviews may use the report stem as informational fallback. `--round` is unspecified
by default: `--addressed <previous-report>` implies 2 only without an explicit round.
`--round 1 --addressed <external-report>` imports earlier findings as context only.
`review_calibration.py record` follows the same round semantics for QA/panel ingestion before triage.

Round 2 reviews fixes/new code and previous blockers only. Match source path, reachable caller
and failure scenario against prior ledger findings and recorded triage, not defect title alone;
line moves preserve a match. Optional `— id: <prior stable ledger finding ID>` references that
prior finding. Known same-site findings with uncertain identity require triage rather than
silently passing. New blockers require severe impact **and** a blocking matrix cell.
Two rounds is the maximum per milestone, shared across lenses. Reports count active/completed
logical rounds. Failed/timeout reviewer attempts release their reservation for bounded retry:
at most **3 attempts** for the same review input, across `--out` changes. Failed attempts do not
consume a successful round; running/completed reviews cannot duplicate.

The append-only ledger retains raw findings, reviewer ratings, computed decisions, triage
reasons, user corrections and later outcomes. `<run>/backlog.md` tracks Minors.
`python3 scripts/review_calibration.py report --run <run> --run <older-run>` shows blockers
per milestone, over-blocked/missed rates and rounds used, with concrete policy suggestions
above documented thresholds. G4/final reports include summary, unresolved triage needs and
backlog path. Sanitized corrections and aggregate outcome counts become a harness-only
Markdown proposal queued with `feedback.py draft --kind friction --from-file <proposal>
--title 'Calibrate review acceptance from observed outcomes' --run-dir <run>`. Check existing
drafts and follow the one-time review offer in "Feedback"; upstream submission requires
explicit user authorization. See the [feedback hook](docs/overlay.md#review-calibration-evidence-and-feedback-hook).
