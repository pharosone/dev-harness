# The project overlay

The core never holds project facts. Everything a product repository adds or changes lives in its
overlay, `harness/project/` (or the directory named by `$HARNESS_PROJECT`, absolute or relative
to the checkout root; the renderer requires it inside the checkout). Core scripts read the
overlay of the checkout they run in and fall back to an empty configuration; nothing in the
overlay is required. A minimal, valid overlay to copy is in `examples/project/`.

Paths in this document are relative to the product checkout. Globs are the core's single glob
dialect (`tripwire.py`): `*` and `?` stay inside one directory, `**` crosses directories, and
`**/` also matches no directory (`**/AGENTS.md` matches `AGENTS.md`).

## Files at a glance

| File | Read by | Without it |
|---|---|---|
| `project.json` | `guard.py`, `harness_sync.py`, `worktree.py`, `render_adapters.py`, `balance.py`, `lanes.py`, `dashboard_herdr.py`, `watch.py`, `feedback.py`, the skills | core guard rules only; no pin (`harness_sync.py` refuses); no project roles or interview rows |
| `tiers.json` | `tier.py` (and `xreview.py` through it) | every changed path is tier 2 (`no tiers.json`) |
| `gates.json` | `gate.py` | `gate.py` exits 2: nothing to run is never reported as a pass |
| `drift.json` | `check_docs_drift.py`, the Stop hook (`runtime_hook.py`) | only the harness-log rule, with its defaults |
| `models.json` | `render_adapters.py`, `xreview.py`, `compare.py` | the core profiles (`harness/core/adapters/models.json`) |
| `router.json` | `model_router.py`, `router_shadow.py`, `balance.py`, `lanes.py` | shadow mode off; existing model selection and dispatch unchanged |
| `architecture.json` | `arch_check.py`; `architecture-reviewer`, `design-review`, `pharos-discover` (Placement) | the check is off (exit 0, and it says so); reviewers take the layers from `architecture.md`, AGENTS.md and the code |
| `architecture.md` | `architecture-reviewer`, `design-review`, `pharos-discover` (Placement) | the map comes from AGENTS.md and the code's own shape; the reviewer reports it under Not covered |
| `rules/*.md` | `render_adapters.py`; agents by path | no path rules |
| `roles/*.md` | `render_adapters.py` | core roles as shipped |
| `skills/` | `render_adapters.py`; the core skills | core skills as shipped |
| `references/qa-gotchas.md` | `qa-verifier`, the QA step of `pharos-dev` | core gotchas only |
| `HARNESS_LOG.md` | `check_docs_drift.py` (harness-log rule) | a harness change is reported until the log exists and changes |
| `tests/` | `harness_sync.py pull`; your `gates.json` | no project harness tests |

An overlay file that exists but cannot be used (bad JSON, wrong type, unknown key, invalid entry)
is an error for `tier.py`, `gate.py`, `check_docs_drift.py`, `arch_check.py`, `watch.py` and `lanes.py` (exit 2) and for the renderer
(exit 1), never a clean result. The guard is the exception: it prints a note to stderr, skips the
bad part and keeps every core rule, because a broken guard must not lock a session.

The optional shadow router is also advisory: an invalid `router.json` logs a skipped shadow
recommendation and retains the actual static pick. It cannot block or promote a dispatch.

## Efficient testing in every overlay

`harness/core/rules/efficient-testing.md` is a core rule for all projects/stacks and both
adapters, not an opt-in product rule. Product overlays declare actual commands and services;
the principle chooses the cheapest correct isolation and owned logical namespaces on shared
long-lived test services. Record any stronger isolation, reusable setup content hash, scoped
cache exception and measured cold/warm wall/resource cost in
`harness/core/prompts/implementer-brief.md`. QA verifies that evidence and real boundary
coverage. Locally run affected tests before expanding; full suites stay in CI/pre-PR.

Build/result/dependency/image caches stay enabled unless a named input is invisible to that
cache; bypass only its affected test/step. CI may keep stricter hermetic result-cache policy.
The adaptive load guard samples host load and memory, never imposes fixed job slots, and
watch surfaces waiting jobs. Targeted tests and admitted descendants bypass waiting; the
max-wait bound prevents starvation. Gate timing compares successful equivalent commands to
prior rolling medians; >50% slowdown is advisory, never a test failure.

## project.json

One JSON object. Each script reads only its own section; other top-level keys are ignored.

```json
{
  "name": "tasklist",
  "core": {"repo": "https://git.example.com/dev-harness.git", "ref": "v1.4.0", "prefix": "harness/core"},
  "guard": {"deploy_hosts": ["deploy.tasklist.example"], "prod_compose": ["compose.prod*"]},
  "interview": {"extra_rows": [{"item": "Accessibility", "question": "…", "section": "Accessibility",
                                "drafted_by": "a11y-reviewer"}]},
  "roles": {"a11y-reviewer": {"description": "…", "profile": "routine", "sandbox": "read-only",
                              "panel": {"min_tier": 2, "paths_exist": ["web/"]}}},
  "worktree": {"node_dirs": ["web"]},
  "balance": {"min_share": 0.3, "stop_at": 0.85, "hysteresis": 0.15, "min_elapsed": 0.25},
  "lanes": {"herdr_max_agents": 5, "count": "run"},
  "dashboard": {"panes": [{"label": "🖥 worker log", "command": "tail -n 30 -f var/worker.log",
                           "zone": "events"}]}
}
```

The guard protects this file: an edit asks the user on Claude Code and is denied on Codex, because
it configures the guard itself.

### load_guard — adaptive host admission

Optional object; defaults apply without an overlay. Full test gates, `skill_evals.py run`
and full `xreview.py` execution check the actual host before starting. This is not a fixed
slot count and does not lock out another project when capacity remains.

| Key | Default | Meaning / environment override |
|---|---:|---|
| `enabled` | `true` | Disable with `HARNESS_LOAD_GUARD=0`; `1` enables. The kill switch works even with unusable overlay settings. |
| `load_per_core` | `1.5` | Wait at or above this multiple of logical CPU cores' 1-minute load; `HARNESS_LOAD_PER_CORE`. |
| `max_wait_seconds` | `300` | Once this bound expires, start anyway; `HARNESS_LOAD_MAX_WAIT_SECONDS`. |
| `backoff_seconds` | `2` | Short resample interval; `HARNESS_LOAD_BACKOFF_SECONDS`. |
| `min_available_percent` | `10` | Linux available memory threshold (includes reusable page cache); `HARNESS_LOAD_MIN_AVAILABLE_PERCENT`. |
| `max_memory_some_percent` | `10` | Linux memory PSI `some` avg10 percentage; above it is busy; `HARNESS_LOAD_MEMORY_SOME_PERCENT`. |
| `max_memory_full_percent` | `1` | Linux memory PSI `full` avg10 percentage; above it is busy; `HARNESS_LOAD_MEMORY_FULL_PERCENT`. |

`HARNESS_LOAD_GUARD_STATE_DIR` selects the host-wide same-user waiting-job registry; its
normal location is `/tmp/harness-load-guard-<uid>`. The internal
`HARNESS_LOAD_GUARD_ANCESTOR` records process identity and verifies actual ancestry, so
nested heavy work in the admitted tree never waits again. It is not a fixed-slot lease.
`watch.py` surfaces live waiting jobs across checkouts and clears stale/dead job records.
Labels omit raw commands and credentials. Watch emits `LOAD <label> waiting (pid <pid>)`
and shows a waiting count separately from open failures; clearing/admission re-arms the
lane inactivity clock. Observe the queue, not a frozen-job failure. Native probes are bounded; unavailable signals
are unknown/fail-open individually, while any known busy load or memory signal still waits.
macOS uses native VM pressure (normal level 1); Linux also uses memory PSI stalls independently
of available memory. Missing PSI is unknown rather than a normal-memory claim. Load ratio must
be greater than zero and at most 64; max wait is 0–3600 seconds, backoff 0.05–10 seconds and
memory percentages 0–100. Platform details and bounded native-probe behavior are defined by
`harness/core/scripts/load_guard.py`. Invalid explicit settings are configuration errors for execution;
read-only check/report/dry-run and targeted paths do not consult heavy admission.

Example: `"load_guard": {"load_per_core": 1.5, "max_wait_seconds": 300}` in `project.json`.
A busy job prints `machine busy (load L / C cores), waiting up to N s`; after the bound it
starts, so stalled telemetry cannot starve work. CI can use a deliberate hermetic/disabled
configuration without changing its test commands or cached local workflow.

### name

String, optional. `feedback.py` redacts it from public feedback, along with the product checkout
name, case-insensitively as whole words or token parts separated by `-`, `_`, `.`, `/`
or digits. Ordinary words stay intact (`app` does not match `application`). Names shared with
harness scripts or commands preserve those harness terms; `feedback.redact` remains available
for explicit literal private terms.

### core — the pin

Written by `harness_sync.py install`; `pull` updates `ref` once the subtree merge lands.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `repo` | string, required | — | Git URL of the core repository |
| `ref` | string, required | — | the installed tag (or ref) |
| `prefix` | string | `harness/core` | where the subtree lives |

Values reach Git as arguments and may not start with `-`. `status --strict` (the project's CI
check) fails while the tree at `prefix` differs from the tree of `ref`.

### feedback

Optional, read by `feedback.py` and feedback-attached `harness_sync.py propose`. The only key
is `redact`: a list of nonempty literal strings, default `[]`. These terms are removed
case-insensitively from titles and bodies in addition to the mandatory product name, path,
email, IP, URL and secret redaction. Add customer names, person names and private identifiers.
Unknown keys or invalid entries refuse feedback rather than silently dropping privacy rules.

```json
"feedback": {"redact": ["Example Customer", "Example Person", "private-account-id"]}
```

This list never leaves the checkout. Run evidence and drafts stay local in `<run>/feedback`
(`feedback.py --task` or `--run-dir`), else `.harness/feedback`. The sanitizer cannot identify
arbitrary product facts: use only synthetic harness evidence and review the displayed final
text before authorizing an issue or PR. See [Feedback](../README.md#feedback).

### guard

Every key is optional, and every list **adds to** the core rules; an overlay cannot remove one.
Unknown keys and invalid entries print a note and are skipped. The guard reads the overlay of the
command's repository and that of the checkout holding the core.

| Key | Type | Core default | Meaning |
|---|---|---|---|
| `deploy_hosts` | [string] | none | host names or IPv4 addresses; an ssh/scp/sftp/rsync argument naming one, or a subdomain of a name, is a deploy (case-insensitive, label by label: `notdeploy.example.com` does not match `deploy.example.com`) |
| `deploy_users` | [string] | `deploy`, `root` | remote users that make an ssh-family call a deploy (`user@`, `-l USER`, `-o User=USER`) |
| `deploy_host_files` | [string] | `.github/workflows/*deploy*`, `deploy/` | repository-relative globs, or directories ending in `/` (read recursively), whose IPv4 literals are deploy hosts; read at decision time; absolute paths and `..` are rejected |
| `prod_compose` | [string] | none | basename globs of production compose files; `docker compose` / `docker-compose` with a matching file is denied |
| `generated` | [{`path`, `hint`?}] | none | directories of generated output; `path` matches as a run of path segments anywhere, any case; shell writes and file edits there are denied with `"<path> is generated output; <hint>"` (default hint: "edit its source and regenerate it instead of changing it by hand.") |

```json
"guard": {
  "deploy_hosts": ["deploy.tasklist.example"],
  "deploy_users": ["release"],
  "deploy_host_files": ["infra/*.hosts"],
  "prod_compose": ["compose.prod*"],
  "generated": [{"path": "web/src/api-client/", "hint": "run npm run gen in web/ instead."}]
}
```

The core rules (push asks, broad staging, `.env` files, personal agents and skills, skipped
tests, deploy tools, the guard's own files) are listed in `scripts/guard.py`.

### interview — project rows of the requirements interview

Read by `pharos-discover` and `pharos-dev` (agents, not a script; nothing validates it).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `extra_rows` | [object] | `[]` | rows asked on every feature, inserted after the core row Contracts, in order |
| `extra_rows[].item` | string, required | — | row name in the interview table |
| `extra_rows[].question` | string, required | — | the typical question |
| `extra_rows[].section` | string, required | — | a `## <section>` every spec must have (it may read "not applicable: <reason>"); `pharos-dev` refuses a spec without it |
| `extra_rows[].drafted_by` | string | none | a project role (under `roles`) dispatched read-only to draft the section and its open questions |

### roles — project roles

Each key is a role name (`[a-z0-9]+(-[a-z0-9]+)*`) that is not a core role, with a body in
`roles/<name>.md`. A core role is never redeclared here; its project text goes in
`roles/<core-role>.md` (see [roles/](#roles)).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `description` | string, required | — | the agent description (when to dispatch it) |
| `profile` | string, required | — | a profile of the merged models (`mechanical`, `routine`, `implementation`, `critical-review`, `escalation`, or one `models.json` adds); it must resolve for both runtimes |
| `tools` | [string], non-empty | runtime default | the Claude Code tool list; omitted, the agent gets the runtime's default set |
| `sandbox` | `read-only` \| `workspace-write` | `workspace-write` | the Codex `sandbox_mode` |
| `panel` | object | none | read by `pharos-dev`: the role joins the review panel as a read-only lens when every key holds — `min_tier` (integer: the unit's tier is at least this) and `paths_exist` ([path]: at least one exists in the checkout). Without `panel` the role works only where an interview row names it |

### worktree

| Key | Type | Default | Meaning |
|---|---|---|---|
| `node_dirs` | [string] | none | checkout-relative directories, in order, where `worktree.py add NAME --deps` runs `npm ci` in the new lane (absolute paths and `..` are rejected; `--deps` with no entries exits 1) |

### balance — vendor balancing

Read by `balance.py pick`, which picks a worker's vendor inside one profile (the same profile on
both vendors) from how fast each vendor's usage windows burn. Optional; each key replaces its
default. Any other key, a non-number or a value out of range is exit 2.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `min_share` | number in [0, 1] | `0.3` | each vendor keeps at least this share of the run's dispatches (`dispatch.start` by runtime; compare candidates are not counted; no rule before the first dispatch) |
| `stop_at` | number in (0, 1] | `0.85` | a vendor with any current window used at or above this is excluded; when every allowed vendor is excluded the pick exits 6 and the orchestrator asks the user |
| `hysteresis` | number ≥ 0 | `0.15` | the previous pick's vendor stays unless the other's runway is larger by more than this |
| `min_elapsed` | number in [0, 1] | `0.25` | a projected run-out (runway < 1: the window would be used up before its reset) counts only once this fraction of that window has passed; it is soft — it steers work to the other vendor, ahead of `min_share`, but never by itself makes the pick exit 6 |

Runway of a window is (1 − used) / (1 − elapsed fraction); a vendor's runway is its smallest
current window's. A vendor without a current observation is unknown and counts as neutral.

### lanes — herdr agent limit

Read by `lanes.py start` for pane lanes (`HERDR_ENV=1`). Optional; each key replaces its default.
Any other key or an invalid value is exit 2.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `herdr_max_agents` | integer ≥ 1 | `3` | live herdr agents at which a new pane lane waits: report-only work runs headless instead, other work is refused with exit 4 (`queued`) |
| `count` | `"workspace"` or `"run"` | `"workspace"` | what counts toward the limit: every agent in the herdr workspace, the user's own sessions included, or only the live agents this run started |

### dashboard — herdr dashboard panes

Read by `lanes.py dashboard --open` (`dashboard_herdr.py`), which opens the run's herdr tab
`📊 <task>`: a left column of live views (`📊 status`, `🧾 events`) and a right column of the
run's lane panes. Project panes join it, each running its command in the current checkout.
Optional; any other key, an empty label or command, or another zone is exit 2.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `panes` | [{`label`, `command`, `zone`}] | none | `label` (string): the pane's title; `command` (string): run in the pane through `herdr pane run`, e.g. a product's live status loop or a log tail; `zone`: `status` (left column, below the status view), `events` (left column, below the events view) or `side` (top of the right column, above the lanes; the default) |

### watch — lane and product observations

Read by `watch.py --task T [--once] [--every S] [--quiet-repeat S] [--ack D]`. Unknown keys in `watch`
or a check, wrong types and invalid values exit 2. Other top-level project keys are ignored.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `checks` | [check] | `[]` | product commands; each prints zero or more anomaly lines, empty stdout and exit 0 is quiet |
| `checks[].name` | string, required | — | unique single word using letters, digits, `_`, `.`, `-`; starts with a letter or digit |
| `checks[].command` | nonempty string, required | — | shell command run in the product checkout; 60-second timeout kills its process group, with at most 2 seconds for stdout draining and bounded reaping; nonzero/timeout emits `CHECK <name> failed` |
| `checks[].every` | finite number > 0 | `120` | seconds between executions, persisted across `--once` and re-arming; cached anomalies stay open between executions |
| `frozen_minutes` | finite number > 0 | `15` | working lane with unchanged screen and no checkout file activity; idle agents or missing screen evidence are not frozen |
| `stop_command` | nonempty string | absent | the product's own command to stop live work on repeating failures; the orchestrator runs it from the product checkout; the watcher never runs it |

An Outbound-style product supplies its own checks, including worker halt, retry loops and
stale heartbeat detection. Keep commands and anomaly output free of secrets; the output becomes
durable run evidence. Check scripts must print current anomalies on every invocation, allowing
the harness to suppress repeats and detect recovery; they should not deduplicate internally.

```json
{"watch": {
  "checks": [{"name": "pilot-worker", "command": "python3 scripts/pilot_health.py", "every": 120}],
  "frozen_minutes": 15,
  "stop_command": "python3 scripts/pilot_worker.py stop"
}}
```

These example commands belong to the product; replace them with its real health and stop
commands. Watch output is `CHECK pilot-worker <anomaly>` and `CHECK pilot-worker failed` on
nonzero exit. Lane observations include Codex question dialogs, trust/hook reviews, Claude
permission prompts, Herdr blocked state, frozen screens, exits and completed/missing reports.
An early final/progress file is not completion while a lane is working. `done` requires
dispatch end or an idle/done agent with a fresh final report. A request file at
`<run>/lanes/<work>.flag.md` (or an open tripwire flag) produces `LANE <work> needs-input flag`;
remove answered flags to clear the condition. Hook trust reviews produce `needs-input hook-trust`
with the exact `t` action, which the user performs after inspecting the hook.
The file channel also covers native subagent `dispatch.start` events. If the run directory is
outside a worker's writable roots, it writes `<report>.flag.md`; only a new report-side flag
since dispatch start counts. Open unit flags at `<run>/units/<work>-flag.md` count until
`tripwire.py resolve` archives them. Unsafe work filenames are slugged; flag text is never copied.
Activity scans count tracked and untracked-not-ignored files and identical-content rewrites,
excluding `.git` and observer-owned `.harness`, plus report/progress stamps. Ignored build/test
output, `node_modules` and `.venv` do not count. Only a working-indicator line's spinner and
elapsed timer are normalized in the screen hash; tool output and token counters still count.
More than 50,000 files or 5 seconds of listing/stat scanning yields unknown activity and no
freeze. Watch emits `LANE <work> activity-unknown (<why>; frozen cannot be detected)` once per
dispatch so a disabled freeze check is visible. Failed screen/state
reads restart freeze evidence; unreadable evidence never proves unchanged activity. A vanished
agent or dead supervisor is exited until its dispatch ends, even if an early report exists.
No Herdr: only persisted dispatch facts and headless process state are available for lanes;
product checks still execute. The watcher does not call lane status (which can submit a brief
or end a dispatch), answer dialogs, close panes or stop the product.

Every new or repeated condition is a `watch` event with `line`, `condition`, `key`, `repeat`
and either `dispatch`/`work` or `check`. State at `<run>/lanes/_watch.json` persists open
conditions and cadence. Default poll: 30 s; default quiet repeat: 600 s. A cleared condition
re-arms; frozen durations share one key so a changing minute count does not defeat suppression.
The dashboards show the events and `Watch: ✅ quiet` / `Watch: 🚨 N open` (or `not armed`).
HTML shows only the latest 50 watch events; all events remain in `events.jsonl`. A changing
frozen duration does not regenerate the page until an emission or an open-condition change.

After accepting a report, or handling/recovering an ended-no-report lane, acknowledge that
dispatch with `python3 harness/core/scripts/watch.py --task T --ack D`. This is part of lane
acceptance/recovery: it permanently closes its terminal conditions and writes `watch.ack`.
Acknowledgment is idempotent and cannot hide input/frozen alerts. New work uses a new dispatch
id. Poll errors are logged to stderr and `watch.error` and the continuous watcher retries;
`--once` returns 1 on a poll failure. Startup/configuration errors still exit 2. Lane files
briefly unreadable during writes retain prior alerts while other lanes/checks are observed.
New pane starts set `launch_pending` and `launch_pid`; an absent/unrecognized agent while the
launcher is alive does not prove exit, even if registration takes longer than five minutes.
The flag clears after startup completes, allowing immediate detection of later exits. A dead
launcher is an exit; old states without a launcher PID get five minutes of registration grace.

For changing counts or timestamps, emit one JSON object per anomaly instead of plain text:

```json
{"key": "worker-retry-loop", "message": "retry loop: 37 attempts"}
```

Only `key` identifies this condition; changing `message` retains repeat suppression. The
message appears as `CHECK <name> retry loop: 37 attempts`. A quiet check clears the condition
and re-arms it. Plain lines remain supported and their complete text is their identity;
checks with changing numbers should use stable keys. Keep both keys and messages free of secrets.

Claude Code arms Monitor at run start and re-arms on expiry; Codex reads a background watch's
output or polls `--once` each turn and before going idle. Each line gets an action in the same
turn: read the actual dialog; answer questions you can decide from the spec and earlier authorizations;
never answer trust/permission reviews; escalate material decisions to the user. Nudge/cancel a
frozen lane, inspect an exit/missing report, accept a report, or stop repeating live product
failures through `stop_command`. The absence of a stop command requires escalation for the
product's stop procedure. Report actions in 2–3 lines; no output needs no message.

## tiers.json

Read by `tier.py --base REF`. The diff (committed, staged, unstaged and untracked against REF;
a rename counts as both paths) takes the strictest tier of its paths and its size.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `tier3_override` | [glob] | `[]` | Tier 3 wherever it lives (agent instructions, build files); checked first, so no tier-0 override lowers it |
| `tier0_override` | [glob] | `[]` | Tier 0 inside a stricter directory (demo data) |
| `tier3`, `tier2`, `tier1`, `tier0` | [glob] | `[]` | the tiers, checked in this order after the overrides |
| `limits` | {`tier0_lines`, `tier1_lines`}, required | — | non-negative integers: above `tier1_lines` changed lines the diff is at least tier 2, above `tier0_lines` at least tier 1 |
| `comment` | string | — | ignored |

A path takes the tier of the first list it matches; a path no list matches is tier 2
("unclassified"). Lines are added + deleted from `git diff --numstat` plus the lines of untracked
files (binary files count 0). Unknown keys are an error.

```json
{"tier3": ["app/auth/**", "app/migrations/**", ".github/**", "harness/**", ".claude/**"],
 "tier1": ["**/test_*.py", "**/package-lock.json"],
 "tier0": ["docs/**", "**/*.md"],
 "limits": {"tier0_lines": 10, "tier1_lines": 100}}
```

## gates.json

The one copy of the CI gates, run by `gate.py [AREA…] [--changed BASE] [--base REF]
[--keep-going] [--dry-run]`. Top-level keys are areas, run in file order, except the reserved
`comment` (text) and `ci_pin` (read only by `--pin-ci`).

| Key | Type | Default | Meaning |
|---|---|---|---|
| `<area>` | {`paths`, `steps`, optional `services`} | — | one area |
| `<area>.paths` | [string] | — | every input of the area's steps that CI reads: `dir/` is a prefix, a string with `*` or `?` a glob (`**` is every path), anything else an exact file; `--changed BASE` selects the areas whose paths changed |
| `<area>.steps` | [step], non-empty | — | run in order through `quiet.py`, logs under `.harness/logs/` |
| `<area>.services` | [service] | `[]` | TCP liveness probes before any selected gate steps; no probe in `--dry-run` or `--pin-ci` |
| `service` | {`name`, `url_env`, `start`, optional `timeout`} | — | single-line display name, environment variable holding the service URL, manual startup instruction and connect timeout in seconds (default 1, greater than 0 and at most 30) |
| `step.name` | string, required | — | unique in the area, no `/` |
| `step.cwd` | string, required | — | checkout-relative working directory |
| `step.cmd` | [string], required | — | argv, run without a shell; `{base}` in an argument becomes `--base` (default: the `--changed` value, else `origin/main`) |
| `step.expect_empty_stdout` | bool | `false` | exit 0 with any output still fails (a formatter's list mode) |
| `step.expand_globs` | bool | `false` | arguments with `*` or `?` are expanded relative to `cwd`; no match fails |
| `step.scope` | `"full"` or `"targeted"` | inferred | Override full-suite vs narrow-test classification for opaque wrappers; `--targeted` bypasses waiting for an affected-only invocation. |
| `step.ci_form` | [form], non-empty | the step itself | how CI runs this step when it differs on purpose (below) |

Steps check the working tree as it is and write no sources; dependency installs belong to the
checkout's setup. Full known suite commands invoke the adaptive host guard; file/package/
filter commands and non-test checks never wait. `--changed` selects areas rather than cases,
so an area's full command remains heavy; a genuinely affected-only wrapper declares
`scope: "targeted"` or uses `--targeted`. Dry-run and pin-ci never probe, wait or measure.

Each executed step appends execution seconds, status and admission wait to the run directory
selected by `--run-dir`, otherwise the checkout's logs. Comparable successful command/cwd/name
fingerprints share a bounded rolling history in the primary checkout; failures do not establish
a successful baseline. These are runtime output artifacts, not skill instruction inputs:

```text
<run>/gate-timings.jsonl
<checkout>/.harness/logs/gate-timings.jsonl
<primary>/.harness/gate/timings.json
```

After three prior successful samples, a duration over 1.5 times the prior positive median
prints an advisory slowdown note (fast steps included) without changing
the real exit/empty-stdout result. Guard waiting is excluded from execution duration. History
uses file locking and atomic replacement; unavailable timing storage is reported, not a pass
claim about performance. `gate.py` prints one line per step (PASS, FAIL with its digest, SKIP) and exits 0
(all passed), 1 (a step failed) or 2 (usage or configuration error).

For example, a tests area can declare
`"services": [{"name": "test DB", "url_env": "TEST_DATABASE_URL", "start": "docker compose up -d test-db"}]`.
Supply that variable in the shell; the harness never reads `.env` or runs the startup command.
Postgres URLs default to port 5432 (Redis 6379, MySQL 3306, HTTP 80, HTTPS 443); other schemes
need an explicit port. Missing/invalid URLs or a closed endpoint exit 1 before any tests,
even with `--keep-going`: `FAIL test DB at 127.0.0.1:55473 is down; start it with …`.
Credentials and database paths are never printed. This checks TCP reachability, not database
authentication, migrations or protocol readiness; the tests still verify those contracts.

### ci_form and ci_pin — pinning the gates to CI

`gate.py --pin-ci .github/workflows/ci.yml` runs nothing; it checks that `gates.json` is the local
copy of the workflow, step for step (project tests can call `gate.pin_ci(gates, workflow)`, which
returns the problems as a list):

- every gate step runs in CI as a gating command (enabled step and job, not followed by `||`, `|`
  or `&`) in its `cwd`, verbatim or with arguments appended — or as its `ci_form` says;
- every command CI runs is a gate step or declared setup;
- every job runs a gate step or is declared CI-only.

A `ci_form` entry is either `{"cwd", "cmd"}` (CI commands that together stand for the step: CI
runs each in `cwd`, starting with `cmd`; `{base}` matches any one argument) or `{"cwd", "uses"}`
(an action whose `uses:` starts with the prefix, with that `working-directory` input, default `.`).

| `ci_pin` key | Type | Default | Meaning |
|---|---|---|---|
| `setup` | [{`cmd`, `cwd`?, `why`}] | `[]` | CI commands that gate nothing (installs, shell plumbing): `cmd` is an argv prefix, `cwd` omitted means any directory, `why` is required |
| `setup_actions` | [string] | `[]` | `uses:` prefixes that gate nothing (`actions/checkout@`) |
| `ci_only_jobs` | {job: why} | `{}` | jobs with no local gate; naming a missing job or one that runs a gate step is a problem |

The example overlay's `gates.json` is pinned by this workflow (abridged):

```yaml
jobs:
  app:
    steps:
      - uses: actions/checkout@v4
      - run: python3 -c 'pass'                     # app/fmt
      - run: python3 -c 'print(1)'                 # app/test
      - run: |                                     # app/schema, through its ci_form
          python3 -c 'print(20)'
          git diff --exit-code
      - run: python3 harness/core/scripts/check_docs_drift.py --base "origin/${{ github.base_ref || 'main' }}"
  web:
    steps:
      - uses: actions/checkout@v4
      - run: npm ci                                # ci_pin.setup
        working-directory: web
      - uses: example/lint-action@v1               # web/lint, through its ci_form
        with: {working-directory: web}
      - run: python3 -c 'print(4)'                 # web/build
  secrets:                                         # ci_pin.ci_only_jobs
    steps:
      - uses: actions/checkout@v4
```

The reader understands the workflow YAML subset CI files use (block mappings, `- ` step lists,
plain and quoted scalars, `|`/`>` blocks). It checks that each command runs, not its arguments.

## drift.json

Read by `check_docs_drift.py --base REF` and by the Stop hook: a change to a path that a
document restates needs a change to that document in the same branch (committed, staged,
unstaged and untracked work count). Every key is optional; unknown keys, bad shapes, a glob where
a file is expected and a bad regular expression are errors (exit 2).

| Key | Type | Meaning |
|---|---|---|
| `harness_log` | {`path`?, `paths`?} | a change to a path matching `paths` needs a change to `path` (below) |
| `watched` | [{`paths`: [glob], `doc`, `claim`}] | a changed path needs `doc` changed (a settings module and its env table) |
| `structural` | [{`paths`, `doc`, `claim`}] | an added, deleted or renamed path needs `doc` changed (a migration and the data-model document) |
| `new_packages` | [{`paths`, `doc`, `claim`}] | an added path whose directory had no files at the base needs `doc` changed (the repository map) |
| `mirrors` | [{`paths`: [file, file, …], `claim`}] | two or more hand-written mirrors of one contract: a changed member needs every other changed |
| `registries` | [{`registry`, `files`: [glob], `exclude`?: [glob], `route`: regex, `claim`}] | an added line matching `route` in a changed file under `files` (not `exclude`) needs `registry` changed; off while `registry` does not exist |
| `comment` | string | ignored |

`doc`, `registry` and mirror members are exact checkout-relative files; `claim` is the text the
finding prints. `route` is a Python regular expression.

`harness_log` defaults: `path` is `<overlay>/HARNESS_LOG.md`; `paths` is the overlay (`<overlay>/**`),
`harness/**` and the rendered files (`.claude/agents/**`, `.claude/skills/**`, `.claude/rules/**`,
`.claude/hooks/**`, `.claude/settings.json`, `.agents/skills/**`, `.codex/agents/**`,
`.codex/hooks.json`, `.codex/config.toml`). A `paths` list replaces the default. Never counted:
the log itself, the core at `harness/core` (core changes go to the core's `CHANGELOG.md`), and
untracked files under `.claude/` (personal agents and skills).

```json
{"watched": [{"paths": ["app/settings.py"], "doc": "docs/configuration.md", "claim": "the env table"}],
 "mirrors": [{"paths": ["app/schemas.py", "web/src/types.ts"], "claim": "the wire types"}]}
```

## architecture.json

The project's declared architecture, checked by `arch_check.py [--base REF | --all]` (the module
docstring documents the CLI): `--base REF` checks the files changed against REF — committed,
staged, unstaged and untracked, like the other checks — and `--all` the whole tree. Findings print
as `path:line — claim` (or `path — claim` for a file as a whole). Exit `0` clean, `1` findings,
`2` a configuration or usage error. No file: the check is off — exit `0` with a note saying so;
a file that exists but cannot be used is exit `2`, never clean. Add the check to a `gates.json`
area so CI runs it; `pharos-dev` runs it with `--base <milestone base>` at every Tier 2–3
milestone and gives the result to the `architecture-reviewer`.

One JSON object; every key is optional, unknown keys are an error. Globs are the core dialect on
checkout-relative paths.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `layers` | [{`name`, `paths`, `may_depend_on`?}] | `[]` | the layers; a file belongs to the layer whose `paths` match it, and a file matching two layers is an error |
| `layers[].name` | string, required | — | the layer's name, unique, used by `may_depend_on` and in findings |
| `layers[].paths` | [glob], required | — | the files of the layer |
| `layers[].may_depend_on` | [layer name] | `[]` | the other layers this one may import (each must be declared); a dependency on any other layer is a finding at its line |
| `imports` | [{`files`, `pattern`, `resolve`?}] | `[]` | how a file's dependencies read in its language; without an entry for a language, its files have no dependencies to check |
| `imports[].files` | glob, required | — | the files this reader applies to |
| `imports[].pattern` | regex, required | — | a Python regular expression (multi-line mode) with a named group `target`; each match is one dependency at the line where `target` starts |
| `imports[].resolve` | {prefix: path} | `{}` | import prefix → checkout path prefix (the longest prefix wins; a prefix ending in `.` turns the dotted rest into a path); a target starting with `.` is relative to the importing file; a target no prefix matches (a standard or third-party library) is ignored |
| `placement` | [{`kind`, `files`, `allowed`, `claim`}] | `[]` | where a kind of file may live |
| `placement[].kind` | string, required | — | what the rule is about (`migration`, `route`, `test`) |
| `placement[].files` | glob, required | — | the files of that kind |
| `placement[].allowed` | [glob], required | — | where such a file may live; a file outside all of them is a finding |
| `placement[].claim` | string, required | — | the text the finding prints |
| `naming` | [{`files`, `pattern`, `claim`}] | `[]` | how files are named |
| `naming[].files` | glob, required | — | the files the rule is about |
| `naming[].pattern` | regex, required | — | the basename must match it in full |
| `naming[].claim` | string, required | — | the text the finding prints |
| `roots` | [glob] | `[]` | every file under a root must belong to some layer; one in none is a finding |
| `comment` | string | — | ignored |

```json
{"layers": [{"name": "api", "paths": ["app/**"], "may_depend_on": []},
            {"name": "web", "paths": ["web/**"], "may_depend_on": []}],
 "imports": [{"files": "app/**/*.py",
              "pattern": "^\\s*(?:from|import)\\s+(?P<target>\\.*[\\w.]*)",
              "resolve": {"app.": "app/"}}],
 "placement": [{"kind": "migration", "files": "**/*.sql", "allowed": ["app/migrations/*.sql"],
                "claim": "SQL migrations live in app/migrations/"}],
 "naming": [{"files": "app/migrations/*.sql", "pattern": "\\d{4}_[a-z0-9_]+\\.sql",
             "claim": "a migration is named NNNN_snake_case.sql"}],
 "roots": ["app/**", "web/**"]}
```

The script sees what a declaration can state: dependency direction, files outside every layer,
misplaced kinds, names. Whether a new piece is in the right module, does one thing, has a name
that says what it is or re-implements something that exists is the `architecture-reviewer`'s.
The example overlay (`examples/project/architecture.json`) passes the check.

## architecture.md

The project's architecture in prose: its layers and what each owns, where a newcomer finds each
kind of thing, the modules that already exist for shared concerns (so a second one is visible
as such), naming conventions, and structure decisions with their reasons. Free Markdown; no
script reads it. The `architecture-reviewer` reads it with `architecture.json` at each Tier 2–3
milestone, `design-review` builds its layer stack from it, and `pharos-discover` drafts every
spec's `## Placement` from it. Keep it to what `architecture.json` cannot say; AGENTS.md may link
to it instead of restating it.

## models.json

Optional profile overrides, the same shape as `harness/core/adapters/models.json`:
`{"<runtime>": {"<profile>": {"model", "effort"}}}` for runtimes `claude` and `codex`. It is
deep-merged over the core table (nested objects merge, other values replace), so an overlay
changes one value or adds a profile without restating the rest. The renderer uses the merged
table for every agent file; `xreview.py` uses the reviewed checkout's merged table, and
`compare.py` the compared checkout's, for its competitors, arbiter and critics. `balance.py`
uses the same table for the actual static pick. When shadow is enabled, `lanes.py decide`
may read it to describe the actual tuple beside its recommendation; `start` always launches
the explicit model/effort arguments. Dashboards display recorded decisions.

```json
{"claude": {"routine": {"effort": "high"}}}
```

A profile you add must exist for both runtimes when a role uses it.

## router.json

Optional shadow policy, deep-merged over `harness/core/adapters/router.json`. Default
`mode: "off"` preserves actual dispatch and emits no router events. `mode: "shadow"` adds
advisory evidence only; no active mode is accepted. Classifier execution is separately disabled
by default. The configured default classifier is Haiku 5.5 medium, marked **UNVALIDATED**.

The registry lists all supported model/effort tuples, quality bands, runtime availability,
frozen trait/cohort maps, source weights and quota/rework policy. Gemini/Grok are planned.
Unknown resource estimates remain unscored; AA output/decode proxies require explicit opt-in.
See [shadow routing configuration and commands](router-shadow.md) for manual weights, safe
classifier capsules, runtime/effort restrictions, formulas and calibration links.

## rules/

`rules/<name>.md`: path-scoped rules. YAML frontmatter with a non-empty `paths:` (globs) is
required; the body is the rule. Rendered to `.claude/rules/<name>.md` with the frontmatter and the
full text, so Claude Code loads it when it touches a matching path. Other runtimes get the rule
paths in their brief; generated roles are told to read the rules their files need.

```markdown
---
paths: ["app/routes/**/*.py"]
---
Every route validates its body with a model from `app/schemas.py`.
```

## roles/

- `roles/<core-role>.md` (one of `implementer`, `simplifier`, `qa-verifier`, `perf-analyst`,
  `security-auditor`, `ui-design-verifier`, `investigator`, `architecture-reviewer`): the
  project's specifics for that role, appended to
  the core role under `## Project specifics`. Write the body only, without that heading. Write
  context the role's model of a change needs — what the product holds, its users, its trust
  boundaries and where they are enforced, its hot paths and limits, the commands and tests that
  already check things, failures paid for before — not a second checklist: reviewers derive
  what to look for from the change.
- `roles/<project-role>.md`: the whole body of a project role declared under `roles` in
  `project.json`.

A body that is neither a core role nor declared is an error, as is a declaration without a
body. Role text is composed into the agent files themselves (it lands in the subagent's system
prompt, cached with it), so a role edit takes effect after `render_adapters.py` and a session
restart; `--check` catches a missed render.

## skills/

- `skills/<name>/SKILL.md`: a project skill, with frontmatter `name` (equal to its lowercase
  directory name) and `description`. Linked as `.claude/skills/<name>` and `.agents/skills/<name>`.
  A project skill named like a core skill replaces it; the renderer says so on every run.
- `skills/<core-skill>.md` (a file, not a directory): that core skill's project specifics. Each
  core skill reads it first when it exists (`skills/pharos-dev.md`, `skills/submit-pr.md` …). Not
  rendered.
- `references/qa-gotchas.md`: the project's QA gotchas in the shape of
  `harness/core/skills/pharos-dev/references/qa-gotchas.md`; the QA pass reads the matching
  sections of both. A gotcha that holds for any stack is proposed upstream instead.

## HARNESS_LOG.md

The project's harness history: one row per harness change (overlay, rendered files, a core
update) with what changed, where, and the failure that caused it. The drift check's
`harness_log` rule enforces the row. Core changes are not logged here: they go to the core's
`CHANGELOG.md` through `harness_sync.py propose`.

## tests/

`tests/test_*.py`: the project's harness tests (unittest). `harness_sync.py pull` runs them after
every update (`python3 -m unittest discover -p 'test_*.py' -s harness/project/tests`) next to the
core tests and refuses to finish on red. Add the same command to a `gates.json` area so CI runs
it. A typical test pins the gates to CI:

```python
import sys, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "harness/core/scripts"))
import gate

class GatesTest(unittest.TestCase):
    def test_gates_are_the_ci_gates(self):
        self.assertEqual(gate.pin_ci(ROOT / "harness/project/gates.json",
                                     ROOT / ".github/workflows/ci.yml"), [])
```

## How overlay values combine with the core

| Overlay | Core | Result |
|---|---|---|
| `guard` lists | core rules and defaults | added; nothing is removed |
| `interview.extra_rows` | nine core rows | inserted after Contracts, in order |
| `roles` in `project.json` | eight core roles | added; a core role name is an error |
| `roles/<core-role>.md` | the core role | appended under `## Project specifics` |
| `skills/<name>/` | core skills | added; the same name replaces the core skill |
| `rules/` | none | the whole set |
| `models.json` | `adapters/models.json` | deep-merged |
| `tiers.json`, `gates.json`, `worktree.node_dirs` | none | the whole table |
| `feedback.redact` in `project.json` | mandatory privacy patterns | added literal terms; nothing removes built-in redaction |
| `balance` in `project.json` | `min_share` 0.3, `stop_at` 0.85, `hysteresis` 0.15, `min_elapsed` 0.25 | each key given replaces its default |
| `lanes` in `project.json` | `herdr_max_agents` 3, `count` `workspace` | each key given replaces its default |
| `dashboard.panes` in `project.json` | the status and events views | added to their zones, in order |
| `watch` in `project.json` | no product checks; freeze after 15 minutes | each key given replaces its default; stop command is for the orchestrator |
| `architecture.json` | none | the whole declaration; without it `arch_check.py` is off |
| `architecture.md` | none | the whole map, read as context; the core adds nothing to it |
| `drift.json` | the harness-log rule | `harness_log.path` / `paths` replace its defaults; the other rules are the project's |

## What is rendered where

`python3 harness/core/scripts/render_adapters.py [--check]` writes, from core and overlay:

| Output | Source |
|---|---|
| `.claude/agents/<role>.md` | core `roles/<role>.md` + `roles/manifest.json`, overlay `roles/<role>.md`, `project.json` roles, merged models: frontmatter `name`, `description`, `model`, `effort`, `tools`, `omitClaudeMd` |
| `.codex/agents/<role>.toml` | the same: `model`, `model_reasoning_effort`, `sandbox_mode`, `developer_instructions` |
| `.claude/skills/<name>`, `.agents/skills/<name>` | symlinks to `harness/core/skills/<name>` or `harness/project/skills/<name>` |
| `.claude/rules/<name>.md` | `rules/<name>.md` |
| `.claude/settings.json` | hooks: Stop → `runtime_hook.py`; PreToolUse (Bash, Read, Grep, Edit, Write, NotebookEdit) → `guard.py`; PreToolUse(Agent) and PostToolUse(Agent) → `tripwire.py hook` (dispatch events, tripwires); `subagentPromptCacheTtl` |
| `.codex/hooks.json` | hooks: Stop → `runtime_hook.py`; PreToolUse (Bash, apply_patch, Edit, Write) → `guard.py` |
| `.codex/config.toml` | created with a comment when missing; kept (and validated) when present |
| `CLAUDE.md` | `@AGENTS.md` added at the top when missing; the rest is kept |

Every generated file carries a `Generated by harness/core/scripts/render_adapters.py` marker and
a `Source:` line. Personal hooks, settings keys and untracked personal agents and skills are kept;
a non-link where a skill link goes is refused. Generated files and links the sources no longer
produce are removed. The renderer refuses to run with the core outside the checkout or on the
core repository itself.

## Skill eval runner configuration

Optional `project.json → skills`, read by `skill_evals.py`. Unknown keys, invalid types,
absolute/traversing paths and non-finite or out-of-range tolerance are exit 2.

```json
{"skills": {"dirs": ["skills/**", "hermes/**/skills/**"],
            "eval_dirs": ["evals/skills/"],
            "eval_command": "python3 tools/skill_eval_runner.py",
            "tolerance": 0.05}}
```

`dirs` selects checkout-relative product skill directories/globs holding `SKILL.md` (default
`[]`). Harness skills are always included. `eval_dirs` contains checkout-relative directories
(default `[]`); each `cases.json` maps skill paths to case directories, for example
`{"hermes/agents/skills/voice/SKILL.md": ["voice-tone"]}`. A skill's cases live in one eval
directory. `eval_command` is a command string split into argv without a shell; it receives
`--skill`, `--out` and requested `--model`/`--max-cost-usd`. It must run both arms and output
scores, actual model and total cost according to README "Skill evals". No command is the
default: product `run` errors rather than pretending it evaluated a skill. `tolerance` is a
finite number in 0..1, default 0.05, the largest allowed existing-case score drop.

The cheap product gate step is
`{"name": "skill-evals", "cwd": ".", "cmd": ["python3", "harness/core/scripts/skill_evals.py", "check", "--base", "{base}"]}`.
Include it in an area with `"paths": ["**"]` and pin the same command in product CI.
Keep result history tracked so regressions can be measured against the gate's base ref.

In Codex-only mode, `skill_evals.py run --runtime codex` explicitly uses `eval_command` for
core skills too, with their core-registered cases. No vendor fallback is implicit.

The runner receives `HARNESS_SKILL_EVAL_MANIFEST`, an environment variable pointing to a JSON
file with the checkout-relative skill key, content hash, `cases` (`name` plus absolute `path`),
and `input_files` (canonical labels to absolute paths). Use those registered paths directly,
including for vendored core cases. `run --base` is required. The hash covers the complete skill
directory and transitive declared references; `<skill dir>/eval-inputs.json` declares dynamic
references as skill-relative paths/globs. See README "Skill evals" for the schema-2 contract.

## review — calibrated acceptance (`project.json`)

Read from `project.json` by `xreview.py` and QA/panel ingestion with `review_calibration.py record`.
Unknown keys, an invalid stage or malformed invariants cause exit 2 before review starts.
Other project sections are ignored.

```json
"review": {
  "stage": "pilot",
  "invariants": ["no outbound action without approval", "no public leak"]
}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `stage` | `"pilot"` or `"production"` | `"pilot"` | production blocks verified severe × edge findings |
| `invariants` | array of non-empty single-line strings | `[]` | product properties that must always hold; severe × edge blocks in pilot only when a finding names an exact matching invariant |

The stage and invariants enter the review context. Every finding has six em-dash-separated fields:

```text
- [Critical|Major|Minor] path:line — defect — likelihood/impact — reachable path — failure scenario — evidence: path:line
```

Evidence must cite code read in the review. Runtime parsing normalizes backticks/bold and line
ranges, retaining the original text. Optional trailing fields are `— workaround: none` or a
specific usable workaround, `— invariant: <exact overlay invariant text>`, and
`— id: <prior stable ledger finding ID>` to reference that same prior finding.
Explicit malformed/partial findings remain **verbatim** in `<report>.calibration.md` and the
run ledger. Missing fields or memory-only claims remain Minor until verified; that downgrade
never approves a reviewer's block. Exit 4 requires triage when a blocking claim is incomplete
or a known same-site finding has uncertain prior identity. It is distinct from exit 1 for a
verified computed blocker. Fully verified matrix backlogs can override a reviewer's block to pass.
Only exit 0 approves; exit 2/3 (refusal, failed reviewer, timeout or missing verdict) do not.
Use `workaround: none` to establish that a plausible functional failure has no workaround.
An absent workaround statement does not establish that it cannot be worked around.
`invariant: no public leak` must name an actual overlay invariant; an invented invariant
cannot elevate a pilot edge case.

| Impact / likelihood | will | plausible | edge |
|---|---|---|---|
| severe | block | block | block with an overlay invariant or production stage; otherwise backlog |
| functional | block | block only with no workaround | backlog |
| cosmetic | backlog | backlog | backlog |

`will` means the main path will fail within days under normal operation; `plausible` means
an occasionally used documented path; `edge` requires a rare combination, launch race,
unusual environment or operator mistake. Severe covers unauthorized/wrong-recipient
outbound actions, burned limits/credits or ban risk, leaked secrets/personal data, data loss,
and silently disabled guards/checks. Functional is a recoverable feature failure or visible
wrong result. Cosmetic affects presentation. Verified severe blockers are Critical;
functional blockers are Major; all backlog findings are Minor.

## Review calibration evidence and feedback hook

Persisted xreview calls (`--run-dir <run>` or `--ledger <run>/xreview.jsonl`) require explicit
`--milestone M1`; no generic milestone default can merge unrelated reviews. Standalone reviews
may use the report stem as an informational fallback, without persistent round enforcement.
The two ledgers serve different purposes: `xreview.jsonl` caps vendor review usage, while
`review-ledger.jsonl` retains acceptance evidence and logical round state.

`--round` defaults to unspecified. When omitted it resolves to 2 with `--addressed`, otherwise 1.
An explicit round always wins: `--addressed <previous-report>` implies 2 **only** without one.
`--round 1 --addressed` imports earlier external findings as context, not prior blockers for
round-2 suppression. For example:

```sh
python3 harness/core/scripts/xreview.py --base <base> --writer claude --run-dir <run> \
  --milestone M1 --round 1 --addressed external-review.md --out <run>/reports/M1-xreview.md
python3 harness/core/scripts/review_calibration.py record --run <run> \
  --report <run>/reports/security.md --milestone M1 --round 1 --addressed external-review.md
```

The record CLI has the same round/addressed semantics; it reads the current product overlay,
or `--root <checkout>`. Ingest QA/panel findings before triage. Distinct report paths for split
areas or lenses may share a round; they do not add rounds. At most two logical rounds per
milestone: round 2 checks fixes/new code and previous blockers only, and a new blocker needs
severe impact **and** a blocking matrix cell. Returning to round 1 after round 2 is refused.

A failed/timeout reviewer attempt releases its reservation for bounded retry: at most **three
attempts for the same review input**, including retries with different `--out` paths. Running
or completed reviews cannot duplicate. Round reporting counts active/completed logical rounds;
failed attempts do not consume a successful round. Changing an output path cannot reset the
retry budget or evade the completed-review restriction. Without a run directory the prompt
still constrains scope, but review state cannot persist between invocations.

Round-2 matching uses source path + reachable caller + failure scenario against prior ledger
findings and their recorded triage, not a defect-title match alone. Source/evidence line moves
are normalized. Optional `id` can reference a prior stable ledger finding; use it only for that
same finding. A known same-site finding with uncertain identity needs triage (exit 4), never
silent treatment as a new functional backlog. Do not drop an unchanged prior blocker just
because its title or line number changed.

Every finding record retains stable identity, raw reviewer text/rating, parsed fields,
matrix verdict/reason, overlay snapshot and review round, including explicit malformed/partial
findings. Append-only triage and outcome events retain corrections; reports use the latest
observation of each finding per milestone. The sidecar retains verbatim unresolved claims as
well as the computed decision: a missing field or identity uncertainty is a triage need,
not a successful empty review.

Resolve triage needs and check every blocker's scenario against code before fix-up
(at most five minutes each):

```sh
python3 harness/core/scripts/review_calibration.py triage --run <run> --id ID \
  --decision backlog --likelihood edge --evidence src/worker.py:42 \
  --reason 'Code confirms only a startup race; functional × edge is backlog'
python3 harness/core/scripts/review_calibration.py outcome --run <run> --id ID \
  --outcome failure --source user-report --reason 'Later report reproduced the startup failure'
python3 harness/core/scripts/review_calibration.py outcome --run <run> --id BLOCKER_ID \
  --user-override backlog --source user-report --reason 'Owner accepts the recoverable failure'
python3 harness/core/scripts/review_calibration.py report --run <run> --run <older-run>
```

Triage can fill missing fields with `--reachable-path`, `--scenario`, `--location`,
`--defect` and `--evidence`, as well as corrected ratings/workaround/invariant.
Triage must agree with the recomputed matrix; a user override is recorded separately and
never hidden as a matrix result. Sources are `fix-up`, `incident`, `user-report`, or `review`;
outcomes are `failure`, `harmless`, or `confirmed`. Backlog that later fails is **missed**.
A blocker overridden to backlog or shown harmless is **over-blocked**. An overridden
blocker that later fails can have both flags: each represents different evidence. Override
alone does not invent a later outcome. Link an outcome to its finding ID whenever a later
fix-up, incident or user report provides evidence; unresolved items stay unresolved.

Reports show blockers per milestone, active/completed logical rounds used and rates per
run/across runs, with observed outcome counts; failed attempts do not add a successful round.
Over-block rate is over-blocked / all computed-or-triaged
blockers; missed rate is missed / all accepted backlog items, including override-to-backlog.
Zero denominators yield 0. Unknown outcomes remain in the denominator and observation
counts make that limitation visible. By default, suggestions start above 25% over-blocked
or 10% missed, with at least three observed outcomes in the relevant group. Override with
`--over-block-threshold`, `--missed-threshold`, `--min-outcomes`; `--json` provides data.
Suggestions name the affected cell or propose an invariant; they never edit defaults.

Minors go to `<run>/backlog.md`. The script maintains a marked section while preserving
owner notes. The orchestrator fixes Minors costing at most 15 minutes in the same fix-up;
remaining items and the backlog path appear in G4 and the final report along with the
calibration summary and unresolved triage needs, including verbatim malformed/partial claims.
A missing/corrupt ledger report is an error, not a zero-error success.

A correction becomes a fixture with an explicitly sanitized JSON file containing only
`name`, a canonical `finding` string, and `review` (`stage`, `invariants`):

```sh
python3 harness/core/scripts/review_calibration.py fixture --run <run> --id ID \
  --case sanitized-case.json --out <run>/calibration-fixtures.json
```

The exported case adds the corrected expected decision and generic `user-correction`
provenance. Raw ledger content is never copied. Private paths, recipients, URLs and common
credential prefixes in submitted cases are refused; synthetic relative paths and invented
product names belong in fixtures. To reference a prior blocker in round 2, retain its reachable
caller and failure scenario or supply its stable ledger `id`; the title alone cannot establish
identity, and source/evidence line shifts do not make it a new finding.
A corrected expectation may deliberately disagree with
the current matrix: that is evidence for a proposed default change, not automatic approval.

**Feedback hook:** `feedback.py draft` accepts `--kind`, `--title`, `--from-file`, and
`--run-dir`. It expects a harness-only Markdown proposal, rather than a raw ledger or
fixture array. Check the full `feedback.py list --run-dir <run>` first for the same issue,
including declined/sent drafts; follow the one-time offer and publication procedure in
README "Feedback". Build `<run>/calibration-proposal.md`
from the sanitized fixture and aggregate counts (omit private run paths), using these
headings: `What happened`, `Expected`, `Repro (minimal, harness-only)`, `Evidence`, `Proposal`.
The evidence includes the current matrix decision, corrected expectation, outcome rates
and a synthetic reproducer. Create a local reviewable draft:

```sh
python3 harness/core/scripts/feedback.py draft --kind friction \
  --title 'Calibrate review acceptance from observed outcomes' \
  --from-file <run>/calibration-proposal.md --run-dir <run>
```

Retain the sanitized fixture array and proposal alongside the queued draft. An upstream
proposal should include a cell/invariant change,
before/after fixture decisions and the rate evidence. Export does not send or publish
anything. The user approves any upstream submission. Run the fixture tests and skill evals
before adopting a new shared default, and retain the old evidence for comparison.
