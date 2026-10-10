# Isolated shadow scorer

The owner-authorized Part B scorer reads local evidence and computes a shadow recommendation. Default `mode: off`; only `off` and `shadow` are accepted. It performs no dispatch, network request, classifier call, acceptance calibration or lead experiment. Root owns dispatch/events and still uses the existing static decision.

`load_context(root)` returns `policy`, flattened `dataset`, `policy_hash`, and `data_hash`. Core policy lives in `adapters/router.json`; `harness/project/router.json` (resolved by `project.overlay_dir(root)`) deep-merges explicitly supported fields. Lists replace, and `followup_shares` replaces its complete mapping. Unknown fields, duplicate JSON keys, invalid finite types, unsupported schemas and inconsistent hashes fail. `make_context(policy, dataset)` validates and rehashes an intentional in-memory override. `score(...)` is pure and JSON serializable; caller-owned context/usage/estimates are not mutated.

```python
context = model_router.load_context(root)
report = model_router.score(
    context, profile="critical-review", tier=3,
    weights={"terminal": 0.60, "tool_workflow": 0.25, "review": 0.15},
    usage=usage_report, now=epoch_seconds,
    allowed_runtimes=["claude", "codex"], not_runtime=None,
)
```

The classifier fields `enabled`, `model`, `effort`, `timeout_seconds`, `max_budget_usd`, and `cache` are exposed for root's wrapper; this module never executes classification. Invalid load/config data raises `RouterError`; invalid score arguments return `status: invalid` with a null recommendation. The root wrapper retains static dispatch on failure.

## Admission and evidence

Expand the registry model × categorical effort slots, then preserve support/reference/exclusion information for each. API support, implementation availability, account status and model-specific account efforts are separate. Unknown account access is disclosed for shadow analysis; explicitly unavailable access excludes. Gemini/Grok remain planned and cannot be selected by the default registry. Command protocol metadata is configurable and never executed by this module.

The frozen reference is AA Intelligence v4.3.2, exact effort, source setting, harness/cohort and snapshot. It admits candidates once and is excluded from task-quality summands.

| Profile | Reference band | Task quality floor | Baseline conditional rework CU |
|---|---|---:|---:|
| mechanical | [0,40) | .20 | 2 |
| routine | [40,48) | .45 | 10 |
| implementation | [48,52) | .50 | 30 |
| critical-review | [51.5,56.5) | .55 | 120 |
| escalation / deep-reasoning | [52,100] | .58 | 240 |

Overlay bounds/floors/rework may narrow or strengthen these approved constraints; they cannot widen a band, lower a floor/minimum, fabricate support or insert an out-of-band effort. `effort_limits` explicitly narrows per-profile/model membership. Tier0/1/2/3 rework minima are 2/10/30/120 CU, enforced even if a tier3 task is called routine. Retry metadata can exclude an unchanged failed tuple and require strictly stronger general reference capability for escalation. A task-specific exception needs an explicit reason and still cannot bypass support/band/floor constraints.

Default independently sourced maps are Terminal-Bench4.0 for terminal; AutomationBench-AA for tool workflow; HLE no-tools as a review reasoning proxy; AA-LCR for long context; AA-Omniscience accuracy for factual; GDP.pdf all-pass as an instruction/document-contract proxy. Repository edit, polyglot, algorithmic, browser and web research defaults are explicit coverage gaps. Inactive SciCode, composite intelligence, preference Elo and observational agent scores do not enter `p0`. Vendor-source weight is zero, independent weight one. No guessed reliability discount is admitted. Review/factual/instruction proxies do not establish security, live fact-checking or pure instruction-benchmark accuracy.

Maps are frozen before task scoring, with declared benchmark, cohort, harness, unit, required protocol fields and exact per-tuple serving setting. Missing/unknown fields and multiple exact rows exclude the tuple as insufficient evidence. A missing trait is never dropped or renormalized away. Active traits cannot count the same metric twice. Every scored tuple must meet the task floor. Fewer than two fully comparable tuples is a forced/static choice, not a learned winner. Full-tuple fallback hints stay in-band but unknown quality still requires manual disposition; a hint does not waive the task floor.

## Continuous scarcity and full expected cost

For each active window, `r=(1-u)/max(1-e,.01)` is a calendar-budget pressure index, not time to exhaustion. The multiplier is:

```text
h(e) = clamp(e/.25, 0, 1)
M = min(50, (1-u)^-2 * (1 + 2*h(e)*max(0, 1/r - 1)))
```

The runout term warms continuously. Known stop-line breaches at used≥.85 survive an unknown outer status until reset. Expired windows are removed. With no usable window, the disclosed provisional state is u=.50,r=1, giving M=4; unknown capacity is not fresh/free. Planned unused buckets remain visible but do not falsely flag fresh active Claude/Codex usage as unknown.

Quota opportunity cost is `max_window(base_price * M * window_weighted_tokens / 1000)`, with default base price .01 CU/1K. It is the maximum indexed product, not maximum multiplier times an unspecified workload. Runtime aliases sharing one bucket do not multiply charges. All consumed windows are checked, even if their price product is not the maximum. API prices are not used for subscription quota conversion.

```text
p0 = clip(1-Q, .02, .98)                 # uncalibrated benchmark error prior
A = C_quota + .10*elapsed_minutes + initial_review_cu
R0 = max(profile allowance, tier minimum)
R = R0 * (.90 + .10*M_followup)
ETC = A + p0*R
```

`R` represents the whole conditional repair/retest/review/escalation/incident chain through acceptance or disposition, not one retry. No geometric multiplier is added. Followup shares are baseline compute-opportunity/normalized-consumption shares, not call counts; default50/50 Claude/Codex is an explicit uncalibrated assumption. Indexed followup workloads are priced using the same maximum-product rule and normalized against the same baseline workload. Missing feasible continuation evidence remains unscored, not cheap.

Minimize unrounded ETC. Only ties within 1e-6 CU use higher task quality, then lower elapsed estimate, then stable full tuple key. There is no quality deadband or extra vendor-balance override. Floors and mandatory review/process constraints are never sold for lower cost.

## Resource estimates and capacity

By default `estimates.proxy_mode: disabled`: qualifying candidates retain `reference_index`, task `quality`, `p0`, provenance and missing resource reasons, but have no expected cost or cost winner. If any otherwise qualifying candidate lacks estimates, partial cost coverage cannot silently crown a measured competitor.

An explicit scenario opt-in is:

```json
{"proxy_mode":"aa_output_decode", "initial_review_cu":10}
```

This uses AA suite output tokens once (reasoning is already included), derived decode minutes and equal output-only quota weights. Input/cache/tools and token-to-window-share conversion are unknown, not zero. Capacity is labelled unmeasured. The latency proxy omits first-token delay/overhead, is not measured agent wall time, and is not a 12-minute lead forecast. API dollars never become Max/Codex quota. No proxy is enabled silently by default policy.

Candidate-specific `kind: measured|bounded` estimates need elapsed minutes, initial review CU, every writer `quota_windows` entry (`weighted_tokens`, `upper_fraction`, `reserve_fraction`), and `continuation_feasible`. They also need `followup_quota_windows` for every positive continuation bucket/window. Writer and same-bucket followup fractional bounds are combined; other-bucket continuation bounds are independently checked against remaining stop-line capacity. `continuation_feasible: true` alone is not enough. Missing bounds remain unscored; breaches are excluded. Optional `followup_window_tokens` must agree with the bound workloads. All reservation accounting is a caller-supplied measured/bounded continuation contract, not empirically proven by the scorer.

In single-runtime mode the default50/50 continuation is infeasible. Provide an explicitly feasible `followup_shares: {"codex":1}` (or Claude-only mix); its whole mapping replaces defaults. Planned/unavailable/stopped followups still exclude. `not_runtime` excludes the writer runtime, while permitted cross-runtime review can remain in the continuation.

The optional `deadline_minutes` is a hard bound against the supplied elapsed estimate. AA decode can supply that comparison only after explicit proxy opt-in and remains labelled a proxy. Root owns process constraints and real verification/acceptance authority.

## Reproduced planning scenarios

| Scenario | Shadow tuple | Expected total CU |
|---|---|---:|
| Routine factual, Claude scarce | Codex / GPT-6.1 Sol / medium | 14.76391942726441 |
| Tier3 Unit20, Claude scarce | Codex / GPT-6 Astra / xhigh | 170.0198914355302 |
| Tier3 Unit20, both fresh | Claude / Sonnet5.5 / max | 59.6900000020667 |

The scenario states are fresh u=.20,e=.30 (M=1.5625), scarce Claude u=.75,e=.50 (M=48), review allowances1/10 CU, default50/50 continuation, and explicit AA output/decode proxy. Scarce conditional tier3 rework is405.375 CU; fresh126.75. These are uncalibrated illustrations, not observed incident rates, quota bills or superiority claims. Root's actual dispatch remains static in shadow mode.

Outputs retain status/recommendation/reason/candidates/evidence/policy_hash/data_hash/assumptions plus estimate hash and diagnostic usage. Root supplies actual dispatch/attempt/decision IDs outside this module. Policy/data hashes pin the content used by the pure scorer.

Checks: `python3 -m unittest discover -s tests -p test_model_router.py -v`; public scrub after staging authored files. Root owns broader integration/release CI. No skills were changed and no live calls belong to these tests.
