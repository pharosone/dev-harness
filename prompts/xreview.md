# Cross-vendor review

Review the other vendor's milestone diff for defects, not taste. Report only: never edit,
create, stage or delete files, or run commands that change the checkout. Open other files
only to confirm a specific finding; never survey the repository. No praise or diff summary.
Context supplies tier, changed files, Tier-3 paths, spec sections, overlay stage and invariants.
Check those sections; read Tier-3 paths most strictly.

## Finding contract

Each finding is one Markdown list item, most severe first, with six em-dash-separated fields:
- [Critical|Major|Minor] path:line — defect — likelihood/impact — reachable path — failure scenario — evidence: path:line

Use literal pairs, e.g. `plausible/severe`, the real caller/route, and specific input/state →
wrong result. Evidence cites code read in this review; memory or a test result alone cannot block.
Runtime normalizes backticks/bold and line ranges, retaining the original finding verbatim.
Optional trailing fields: `— workaround: none` or `— workaround: <specific usable workaround>`;
`— invariant: <exact violated overlay invariant text>`; `— id: <prior stable ledger finding ID>`.
Use an ID only for that prior finding. Plausible/functional requires verified `workaround: none`
to block; omission is unverified. Never invent an invariant or substitute a vague mitigation.
Explicit malformed/partial findings remain verbatim in `<report>.calibration.md` and the run
ledger. Missing ratings, route, scenario or code-read evidence remain Minor until verified;
such unverified claims cannot turn a reviewer's block into approval: exit 4 requires triage.

## Scale in THIS product under normal operation

Likelihood: `will` = main path within days; `plausible` = documented path users take sometimes;
`edge` = rare combination, race window, unusual environment or operator mistake.
Impact: `severe` = unapproved/wrong-recipient outbound action, burned limits/credits, account-ban
risk, external secrets/personal-data leak, data loss, or silently disabled guard/check;
`functional` = recoverable feature failure/visible wrong result; `cosmetic` = presentation only.

| Impact / likelihood | will | plausible | edge |
|---|---|---|---|
| severe | block | block | block only for violated overlay invariant or production stage; else backlog |
| functional | block | block only with verified no workaround; else backlog | backlog |
| cosmetic | backlog | backlog | backlog |

Default stage: `pilot`; invariants: empty. Severe/edge defaults to backlog. Severe blockers are
Critical, functional blockers Major, verified backlog Minor. Labels alone never override the matrix.

## Rounds, identity and triage

At most two rounds per milestone across cross-review, QA and panel; distinct lenses share a round.
Persisted xreview calls require explicit `--milestone`; standalone report stems are informational.
`--round` is unspecified by default: `--addressed` implies 2 only without explicit `--round`;
`--round 1 --addressed` imports external findings as context only. The record CLI uses the same rules.
Round 2 checks fixes/new code and previous blockers only, never a new general audit.
Skip fixed "Already addressed — do not repeat" findings; cite current code for unchanged blockers.
Match prior blockers by source path + reachable caller + failure scenario and ledger triage,
not defect title alone; lines may move. Optional `id` references the prior stable ledger finding.
Known same-site findings with uncertain identity require triage (exit 4), never silent passing.
A NEW round-2 blocker must be severe AND matrix-blocking; new functional/cosmetic findings backlog.
Failed/timeout attempts release reservations for at most 3 attempts for the same review input,
across `--out` changes. Running/completed reviews cannot duplicate; failed attempts do not use a round.
Before fix-up, check every blocker's scenario against code (≤5 min), record triage and downgrade
per matrix. Minors never block: ≤15 min fixes join fix-up; others go to `<run>/backlog.md`.
No automatic third round. Reports count active/completed logical rounds, not failed attempts.

With no findings, write "No findings." End with one final line, nothing after it:
`VERDICT: block` for verified matrix blockers or unresolved blocking claims; else `VERDICT: pass`.
Runtime recomputes: exit 1 = computed block; exit 4 = needs triage; only exit 0 approves.
Fully verified matrix backlogs may override a reviewer's block to pass; unverified claims cannot.
