# Enable shadow recommendations without changing dispatch

The 1.5.0 shadow router records what it would choose. `balance.py` still chooses the real
vendor using its existing quota/share rules; `lanes.py` still launches the supplied model,
effort and launcher. No recommendation is copied into a worker command. Gemini and Grok
remain planned runtimes. Classifier experiment C and the lead pilot are separate, unrun work.

## Enable or disable

Create `harness/project/router.json` (or the overlay selected by `HARNESS_PROJECT`):

```json
{
  "mode": "shadow",
  "classifier": {"enabled": false}
}
```

Missing configuration or `{"mode":"off"}` preserves the pre-router output/event contracts:
no benchmark load, classifier process, classifier cache or router event. The actual static
profile mapping stays in `adapters/models.json` plus the existing `models.json` overlay.
There is no active-router mode. Invalid shadow configuration yields an advisory skipped
result; it does not invalidate the actual dispatch.

With no calibrated resource estimates, candidate task quality is shown but expected-cost
selection remains `unscored`. A recorded absence of evidence is useful shadow telemetry.
Do not substitute API prices for Max/Codex quota.

For a clearly labeled output-only scenario, opt into the AA resource proxy explicitly:

```json
{
  "mode": "shadow",
  "classifier": {"enabled": false},
  "estimates": {"proxy_mode": "aa_output_decode", "initial_review_cu": 10}
}
```

This uses weighted output tokens and derived decode time, which excludes first-token delay
and tool overhead. Input/cache/tool quota and true wall time remain unmeasured; capacity is
not verified by that proxy. Its result is a scenario, never a calibrated dispatch recommendation.
Use candidate-specific bounded/measured estimates after collecting the linked evidence.

## Supply weights or an explicit task capsule

No classifier is needed for manually supplied, audited task weights:

```sh
python3 harness/core/scripts/balance.py pick --task demo --profile critical-review \
  --role implementer --tier 3 \
  --router-weights '{"terminal":0.6,"tool_workflow":0.25,"review":0.15}'
```

The top-level `runtime`, `model`, `effort` and `reason` remain the actual static choice.
The additive `shadow` object records its separate recommendation, status, full candidate
evidence, policy/data hashes and assumptions. Manual weights are `HUMAN_SUPPLIED`, not gold
classifier labels. Model scores, costs and usage do not enter the classifier prompt.

`lanes.py decide` accepts `--task`, `--profile`, `--tier`, `--brief`, and `--router-weights`.
`lanes.py start` accepts `--profile` and `--router-weights` beside its existing brief/tier
arguments. `--profile` affects only shadow scoring. It never overrides `--model` or `--effort`.
Without a task, `decide` can still choose the launcher but cannot append run evidence.
Missing grade/tier or task context is an explicit shadow skip; risk is not guessed as zero.

To opt into classification, merge this into `router.json`:

```json
{
  "mode": "shadow",
  "classifier": {
    "enabled": true,
    "model": "claude-haiku-5-5",
    "effort": "medium",
    "timeout_seconds": 20,
    "max_budget_usd": 0.05,
    "cache": true
  }
}
```

Every classifier result is **UNVALIDATED**. This implementation does not select Haiku on the
basis of accuracy and does not run experiment C. A configured classifier is one subscription
Claude Code print call; no API key is required. API-key/third-party endpoint environment
overrides are removed for this child, while subscription authentication remains available.
The process uses `--safe-mode`, a replacement compact system prompt, empty tools, strict
empty MCP configuration, disabled skills, no persistent session, and a temporary working
directory. `--bare` is not used because it disables subscription authentication. These flags
were checked against local CLI help and the [CLI reference](https://code.claude.com/docs/en/cli-reference).

The API-equivalent per-call ceiling is a CLI guard, not a measured subscription quota cap;
the last request can overshoot it. Timeout kills the classifier process group. Failure,
wrong/missing resolved model identity, invalid JSON/schema or insufficient context logs a
skip and makes no repair call. Returned structured-output formatting is allowed; ordinary
tool calls are rejected. A corrupt cache skips classification rather than triggering a paid retry.

Pass `--brief` a UTF-8 JSON capsule such as:

```json
{
  "goal": "Isolate the assistant runtime in its own Compose service",
  "paths": ["infra/compose.yaml", "infra/assistant/**"],
  "acceptance_criteria": ["No published ports", "Tool permissions are tested"]
}
```

Role, profile and tier come from command arguments. Only goal, repository-relative paths and
acceptance criteria are retained. Other JSON fields are discarded. Markdown briefs must have
an explicit `Goal`/`Objective` section; only that section plus `Contract`/`Acceptance` and
`Paths`/`Allowed paths` sections are read. Referenced files are never opened. Either input is
bounded to 8 KiB without truncation. Common credential patterns, emails, URLs and absolute
paths are removed, but this is not a universal personal-data detector: prepare capsules without
account identities or private conversation excerpts. Missing explicit goals produce a skip.

The cache under the run's `router-cache/` pins sanitized capsule hash, taxonomy/prompt/schema,
model and effort. It stores validated classification and cost metadata, not the capsule text.
Data and pricing changes reuse classification but recompute scoring; policy/data hashes are
separate. Symlinked evidence/cache paths are rejected. Cache hits have zero new classifier
usage and preserve the prior resolved identity; every fresh call must confirm its pinned model.

## Registry, bands and cost

`adapters/router.json` is the versioned default registry and policy; `router.json` deep-merges
its supported fields. All API-supported effort settings are expanded, then filtered by the
AA v4.3.2 reference band, task-quality floor, runtime availability and explicit restrictions.
Support observed in an API is distinct from actual CLI/account verification. Planned runtimes
are excluded; after their adapter and support evidence exist, activation is configuration.

| Profile | AA reference band | Task-quality floor | Conditional rework baseline, CU |
|---|---:|---:|---:|
| mechanical | `[0,40)` | .20 | 2 |
| routine | `[40,48)` | .45 | 10 |
| implementation | `[48,52)` | .50 | 30 |
| critical-review | `[51.5,56.5)` | .55 | 120 |

| Profile | AA reference band | Task-quality floor | Conditional rework baseline, CU |
|---|---:|---:|---:|
| escalation | `[52,100]` | .58 | 240 |
| deep-reasoning | `[52,100]` | .58 | 240 |

The reference index only admits tuples. Frozen independent task cohorts produce weighted
task `Q`; vendor claims and Arena preference scores have zero weight in the initial failure
prior. Missing exact effort/scaffold/version evidence stays missing; no neighbor score is
borrowed. `p0=clip(1-Q,.02,.98)` is an uncalibrated prior. Lower profiles cannot select above
their band ceiling; changing a profile requires an explicit escalation.

For each usage window: `r=(1-u)/max(1-e,.01)`, `h=min(1,max(0,e)/.25)`, and
`M=min(50,(1-u)^-2 * (1+2*h*max(0,1/r-1)))`. The pressure index `r` is not actual
time-to-runout. Unknown usage is explicitly unknown, with the .50-used/r=1 scenario
assumption; it is not free quota. `stop_at=.85` is a hard exclusion.

Quota cost is the maximum applicable window's **price × consumption** product:
`max_w(.01 * M_w * weighted_tokens_w/1000)` CU, with per-window consumption/reserve
bounds checked when supplied. A CU is an operator-minute-equivalent planning unit, not USD.
`A=quota_cost + .10*elapsed_minutes + initial_review_cost`.
`R=max(profile_R0,tier_R0) * (.90+.10*M_followup)`, with tier 0/1/2/3 minima
2/10/30/120 and declared baseline-compute shares over the whole followup chain.
Required cross-vendor reviews consume their own vendor's quota. `A+p0*R` includes all
conditional repair/review/risk work; do not apply another geometric retry multiplier.

The minimum unrounded expected cost wins subject to all floors and constraints. Only ties
within `1e-6 CU` use higher Q, lower elapsed time, then stable tuple key. Continuous scarcity
is charged even outside a quality tie. Complete per-candidate evidence and assumptions are
retained; small modeled gaps are not claims of statistical superiority.

## Link real outcomes

The run logs additive `router.shadow` events. Both dashboards render
`⚖ shadow: would pick … because …`, show the actual tuple separately and expose missing
data/UNVALIDATED classification. Scoring and classification failures remain visible as skips.
Full evidence is in `events.jsonl`; the dashboards are summaries.

When shadow is enabled, `dispatch.start` and the lane state add attempt/root/parent IDs,
selected actual tuple, profile/tier and policy/data hashes. `--router-decision-id` links a
prior same-run recommendation for the same actual tuple. `--parent-attempt-id` explicitly
links a repair to the preceding dispatch; the root is derived from that existing lineage,
or checked against `--root-attempt-id`. Invalid links are skipped, never silently invented.
A subagent decision has no dispatch until the orchestrator records one; the explicit
calibration link command can associate that later dispatch.

Use the [calibration helper](benchmarks/router-calibration.md) to record acceptance, rejection,
fixups, incidents and review verdict references. Process `done` is never acceptance. Review
references resolve against the 1.4.0 review ledger; a review reservation is not a verdict.
The report exposes labeled/censored counts, linked review/fixup evidence and missing costs.
No automatic probability or quota fit is fabricated from incomplete data.
