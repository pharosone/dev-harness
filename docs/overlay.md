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
| `project.json` | `guard.py`, `harness_sync.py`, `worktree.py`, `render_adapters.py`, `balance.py`, `lanes.py`, `dashboard_herdr.py`, `feedback.py`, the skills | core guard rules only; no pin (`harness_sync.py` refuses); no project roles or interview rows |
| `tiers.json` | `tier.py` (and `xreview.py` through it) | every changed path is tier 2 (`no tiers.json`) |
| `gates.json` | `gate.py` | `gate.py` exits 2: nothing to run is never reported as a pass |
| `drift.json` | `check_docs_drift.py`, the Stop hook (`runtime_hook.py`) | only the harness-log rule, with its defaults |
| `models.json` | `render_adapters.py`, `xreview.py`, `compare.py` | the core profiles (`harness/core/adapters/models.json`) |
| `architecture.json` | `arch_check.py`; `architecture-reviewer`, `design-review`, `pharos-discover` (Placement) | the check is off (exit 0, and it says so); reviewers take the layers from `architecture.md`, AGENTS.md and the code |
| `architecture.md` | `architecture-reviewer`, `design-review`, `pharos-discover` (Placement) | the map comes from AGENTS.md and the code's own shape; the reviewer reports it under Not covered |
| `rules/*.md` | `render_adapters.py`; agents by path | no path rules |
| `roles/*.md` | `render_adapters.py` | core roles as shipped |
| `skills/` | `render_adapters.py`; the core skills | core skills as shipped |
| `references/qa-gotchas.md` | `qa-verifier`, the QA step of `pharos-dev` | core gotchas only |
| `HARNESS_LOG.md` | `check_docs_drift.py` (harness-log rule) | a harness change is reported until the log exists and changes |
| `tests/` | `harness_sync.py pull`; your `gates.json` | no project harness tests |

An overlay file that exists but cannot be used (bad JSON, wrong type, unknown key, invalid entry)
is an error for `tier.py`, `gate.py`, `check_docs_drift.py`, `arch_check.py` and `lanes.py` (exit 2) and for the renderer
(exit 1), never a clean result. The guard is the exception: it prints a note to stderr, skips the
bad part and keeps every core rule, because a broken guard must not lock a session.

## project.json

One JSON object. Each script reads only its own section; other top-level keys are ignored.

```json
{
  "name": "tasklist",
  "core": {"repo": "https://git.example.com/dev-harness.git", "ref": "v1.0.0", "prefix": "harness/core"},
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

### name

String, optional. `feedback.py` redacts it from public feedback, along with the product checkout name.

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
| `<area>` | {`paths`, `steps`} exactly | — | one area |
| `<area>.paths` | [string] | — | every input of the area's steps that CI reads: `dir/` is a prefix, a string with `*` or `?` a glob (`**` is every path), anything else an exact file; `--changed BASE` selects the areas whose paths changed |
| `<area>.steps` | [step], non-empty | — | run in order through `quiet.py`, logs under `.harness/logs/` |
| `step.name` | string, required | — | unique in the area, no `/` |
| `step.cwd` | string, required | — | checkout-relative working directory |
| `step.cmd` | [string], required | — | argv, run without a shell; `{base}` in an argument becomes `--base` (default: the `--changed` value, else `origin/main`) |
| `step.expect_empty_stdout` | bool | `false` | exit 0 with any output still fails (a formatter's list mode) |
| `step.expand_globs` | bool | `false` | arguments with `*` or `?` are expanded relative to `cwd`; no match fails |
| `step.ci_form` | [form], non-empty | the step itself | how CI runs this step when it differs on purpose (below) |

Steps check the working tree as it is and write no sources; dependency installs belong to the
checkout's setup. `gate.py` prints one line per step (PASS, FAIL with its digest, SKIP) and exits 0
(all passed), 1 (a step failed) or 2 (usage or configuration error).

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
`compare.py` the compared checkout's, for its competitors, arbiter and critics. `lanes.py` and
`dashboard.py` do not read `models.json` (`lanes.py` reads `project.json → lanes` and
`dashboard` only).

```json
{"claude": {"routine": {"effort": "high"}}}
```

A profile you add must exist for both runtimes when a role uses it.

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
