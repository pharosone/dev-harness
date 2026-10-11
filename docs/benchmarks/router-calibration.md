# Shadow routing calibration evidence

`router_calibration.py` collects explicit evidence and renders aggregate coverage. It does
not select or launch models, change static dispatch, infer acceptance from process exit,
fit a regression, or estimate missing monetary/quota costs. The dashboards show what the
shadow policy would choose separately from the actual static tuple.

## Root event contract

The existing event envelope remains `{v, id, at, type, ...fields}`. For `router.shadow`,
record `decision_id`, `status`, nullable `recommendation`, `reason`, `candidates`, `evidence`,
`policy_hash`, `data_hash`, `assumptions`, optional `classifier`, `actual`, `selected_tuple`,
`profile`, `tier`, and nullable dispatch/attempt/root/parent IDs. Tuples contain runtime,
model and effort. `actual` and `selected_tuple` describe the requested static launch;
they must match `dispatch.start`. They are never replaced by the recommendation or a
canonical identity guessed from an alias.

Write the actual start before its attached shadow event. Repeat immutable linkage on an
end when available. An attached enriched start/shadow pair supplies reportable linkage
without calling the optional `link_attempt` hook. A standalone decision can be linked
later, provided its recorded actual tuple matches the real start.

The HTML page adds a shadow section only when shadow events exist. Terminal status and
event views add `⚖ shadow: would pick ...; actual ...` or an explicit skipped result.
Classifier presence always displays **UNVALIDATED classifier** in this shadow-only phase.
Reasons, assumptions, missing data and provenance are escaped/redacted; terminal and
bidirectional controls are neutralized. Neither display alters selection or run evidence.

## Importable hooks and CLI

Add the scripts directory to the caller's import path, then use:

- `link_attempt(run, dispatch, decision_id, *, root_attempt_id=None, parent_attempt_id=None, event_id=None)`
  writes optional `router.attempt` provenance. Dispatch, decision and exact tuple must exist;
  parent/root references must be explicit, present and acyclic.
- `label_attempt(run, attempt_id, status, *, authority, criteria_id, revision, review=None, event_id=None)`
  writes `router.label`. Allowed statuses are `accepted`, `rejected`, `fixup`, `censored`,
  and `incident`. The caller supplies an explicit acceptance decision and revision;
  `done`, `failed`, `cancelled`, timeout or a review alone never creates that decision.
- `link_fixup(run, parent_attempt_id, child_attempt_id, *, event_id=None)` records a
  `router.fixup` edge only between existing explicitly parent-linked attempts. It does
  not generate an outcome label.
- `review_snapshot(run, reference)` validates the authoritative 1.4 review ledger and
  returns immutable review/result record hashes plus source hash and row numbers.
- `calibration_report(run)` returns read-only aggregates, provenance and uncertainty.

CLI equivalents:

```sh
python3 scripts/router_calibration.py report --run .harness/tasks/example --json
python3 scripts/router_calibration.py link --run .harness/tasks/example \
  --dispatch dispatch-1 --decision-id decision-1
python3 scripts/router_calibration.py label --run .harness/tasks/example \
  --attempt-id dispatch-1 --status rejected --authority operator \
  --criteria-id gate-v1 --revision revision-1
python3 scripts/router_calibration.py fixup --run .harness/tasks/example \
  --parent-attempt-id dispatch-1 --child-attempt-id dispatch-2
```

Tier **0–1** acceptance may use explicit operator authority, criteria ID and accepted
revision without a panel review. It is reported as operator evidence with a warning,
not as a panel pass. Tier **2–3** acceptance requires an actual completed/computed-pass
review from `review-ledger.jsonl`. Every supplied review is validated; a supplied review
supporting positive acceptance must pass even at lower tiers.

A reference supplied as the `review` argument or `--review` JSON has exactly
`{attempt_id, milestone, round, report, verdict}`. Each identity field must match a real
1.4 `review` reservation and its latest later `review_result`. The result must contain
the computed matrix verdict; a later textual reviewer verdict that was overridden does
not change that result. Reservations alone, raw uncomputed pass text, failed results and
needs-triage results cannot justify high-tier acceptance. The helper reads ledger metadata,
not report prose. The caller remains responsible for linking the reviewed milestone to
the explicit accepted unit/revision and criteria; the helper never invents that relationship.

Snapshots retain the historical source hash. If an unrelated review is appended, the
same immutable review/result pair remains valid. If that result is subsequently changed,
the report withholds the old label from outcome eligibility. Older `xreview.jsonl` quota
reservations cannot substitute for the 1.4 authoritative ledger.

## Identity, locking and coverage

Writes use the existing event format and an exclusive `events.jsonl` file lock. Explicit
IDs are preferred at integration boundaries. Exact replay of an ID is a no-op; conflicting
reuse is an error. Without an explicit ID, a stable evidence-content hash is used. Changing
between custom and generated IDs is a new identity, not a replay. No POSIX locking means
writes fail closed; read-only reporting and dashboards remain available. Symlink ledgers
and malformed append targets are refused. Helpers never append to the review ledger.

Coverage separates starts/ends, explicit labels, first substantive outcomes, censored
starts, review links, fixup edges, duplicate ends and incomplete provenance. An ended
process is not accepted; a start without an end is censored at observation, not necessarily
failed or still running. An explicit incident remains distinct from first-pass acceptance.

Tuple groups have `tuple_basis=requested_launch`. End `serving_identity` coverage counts
single/mixed/unverified stream observations and observed effort independently. A single
stream model key does not prove a pure canonical model, rule out safeguard fallback, or
resolve a requested effort. No alias is canonicalized. `proven_serving_tuple_n` remains
zero because this helper does not perform that verification.

Recorded input tokens in lanes telemetry already include cached input; do not add cache
reads again. Reasoning/output and cache-write accounting need provider-specific provenance.
Each resource has observed count, explicit-zero count and observed sum; no observations
means null, not zero. Equivalent spend, charged cost and quota opportunity cost are separate
quantities. Missing costs, confidence intervals, rework estimates and reserves remain null.
`fit_ready` remains false even when explicit outcome coverage grows: this module reports
observability and never asserts that a sparse sample calibrates a model.

Source hashes identify exact read snapshots. Aggregate reports omit raw task/review prose.
Before a later fit, define acceptance/incident horizons, verify resolved serving tuples,
attribute nested and concurrent usage once, complete repair chains and quota deltas, and
control for task difficulty, selection policy and original-task clusters. A process-status
ledger or synthetic demo cannot supply those labels.

## Integration responsibility

Root owns balance/lanes/task-state/xreview integration. Preserve static selection, attach
full IDs/profile/tier/policy/data hashes, and call label hooks only at an explicit unit
acceptance/fixup/incident decision. Snapshot actual 1.4 review IDs and computed results.
Call existing dashboard regeneration after an append as appropriate. This helper does
not change process exits, launch flags, grade choices or review policy.
