---
name: pharos-discover
description: "Use when a feature, endpoint, screen or behaviour change in this repository needs requirements, alternative approaches and an approved specification with TDD units; also when a spec lacks its Requirements interview, its Placement, its Taste gates or a section a project interview row requires."
---

# pharos-discover — requirements through approved specification

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/pharos-discover.md` (this skill's project specifics) before following
this procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it. This skill owns the complete discovery flow: interview, alternatives, design,
specification and approval. It requires no external brainstorming plugin.

Reuse answers, constraints and approvals already present in the conversation. A user-approved
proposal is evidence; do not repeat its questions. For a new material choice, present the
recommended option with its consequence and obtain the missing decision. Time pressure changes
how questions are grouped, never which requirements are covered.

## The checklist — every row gets an answer

| # | Item | Typical question |
|---|------|------------------|
| 1 | Goal and user | Who uses it, for what decision or action, what changes for them? |
| 2 | Roles and access | Which roles may read / change it; admin- or operator-only parts; whose data is it (user, team, tenant)? |
| 3 | Scope | What is in v1, what is explicitly out? |
| 4 | UI | Where in the product; which states (loading, empty, error, forbidden, disabled)? |
| 5 | Data | What is stored, for how long, who deletes it; migration impact? |
| 6 | Contracts | Wire types, public API description, SDKs, payloads leaving the system? |
| 7 | Failure and limits | Timeouts, retries, rate limits, caps, what the user sees on failure? |
| 8 | Done when | Which observable results make it finished? |
| 9 | Taste gates | Where do you want to see results and decide the direction? |

### Project rows

A project adds the rows it asks on every feature in `harness/project/project.json`:

```json
{"interview": {"extra_rows": [
  {"item": "<row name>", "question": "<typical question>",
   "section": "<required spec section>", "drafted_by": "<project role, optional>"}
]}}
```

- Project rows come after Contracts, in the order listed; the spec's table numbers all rows
  consecutively. No file, or no `extra_rows`: the nine core rows are the whole checklist.
- `section` is a required section of every spec (`## <section>`). It may read "not applicable:
  <reason>"; `pharos-dev` refuses a spec without it.
- `drafted_by` names a project role (`project.json → roles`) that drafts the section and its
  open questions. Without it, you draft the section and ask its questions yourself.

## The run

Before the first question, initialize the run:
`python3 harness/core/scripts/task_state.py init <task>` (no `--spec`; the spec does not exist
yet). `<run-dir>` in this skill is the directory it prints, `.harness/tasks/<task>` in the
primary checkout (`task_state.py locate <task>` prints it again). Every `task_state.py event` and `compare.py` call below refuses a run that was not
initialized.

## Depth

Ask the mode in the first question batch, recommended option first with the signal behind it:

- **`standard`** — you run discovery on the session's model.
- **`deep`** — before the approaches, a fresh `investigator` on the `deep-reasoning` profile
  (Opus `xhigh` / Astra `xhigh`) maps how the touched code really works (paths, invariants,
  what can break) and drafts the approaches and their trade-offs; before the spec goes to the
  user, the other vendor gives a second opinion on the draft (what it misses, what will not
  hold). Recommend it for Tier-3 paths (`harness/core/scripts/tier.py` on the paths the feature
  will touch), two or more real architectural options, unfamiliar code without specs or tests,
  concurrency, money, security or data migrations, or when the user asks for depth.

Write it as "`deep` (Recommended) — <signal>" or the same for `standard`; record the answer,
model and effort in the spec's interview (row Goal and user, source `user:`) and in `run.md`.
A feature that first needs a question answered (a bug, data for a decision) goes through
`pharos-investigate`, and its report becomes the design's evidence.

## Compare

Ask `compare` in the same question as the depth mode; the default is `off`:

- **`off`** (default) — one discovery as described here.
- **`design`** — two competitors, one per vendor, each write an approach memo from the same
  frozen interview record without seeing the other's; an arbiter that wrote neither compares
  them twice, labels anonymised and the order swapped. Recommend it when there are two or more
  real architectural options with different consequences: "`compare=design` (Recommended) —
  <the options and how their consequences differ>". Without that signal recommend `off`.

`compare=off` leaves the flow unchanged: in `deep`, one investigator and a second opinion, as
before. Record the answer in `run.md` and as the run's first event:
`python3 harness/core/scripts/task_state.py event <task> run.start --data
'{"workflow": "discover", "depth": "<mode>", "compare": "off|design", "runtime_mode": "<mode>"}'`.

## How to run it

1. Read the project's map (AGENTS.md), the applicable `harness/project/rules/*.md` and the
   touched code. Inspect the current implementation before assuming a future contract already
   exists.
2. Map the conversation and repository rules to every row. Ask the depth mode, compare (above)
   and unresolved Goal, Roles, Scope and UI first, using the question mechanism and batch limit supported by the runtime adapter.
   Put the recommended option first, label it "(Recommended)", and state each option's
   consequence.
3. Dispatch the drafting roles of the project rows in parallel, read-only, with established
   answers and the touched code. Each returns a draft section and its unresolved questions.
   If independent execution is unavailable, record that limitation and the outstanding review;
   never label the orchestrator's own draft as an independent review.
4. Ask unresolved Data, Contracts, project rows (with the drafting roles' questions), Failure
   and limits, Done when and Taste gates in supported batches until each row has an answer. For
   Taste gates recommend the default set: G1 the approved spec, G2 the first end-to-end slice on
   demo data, G3 each milestone, G4 before the PR. Reuse decisions already made; do not ask for
   permissions already granted.
5. Write back the requirements, their sources and open risks for correction. In `deep` mode
   start from the investigator's map and approaches. Compare two or three feasible approaches when there is a real architectural choice, with costs and a
   recommendation. When the user already selected an approach, document it and alternatives
   only if new evidence makes them relevant.
   With `compare=design` the approaches come from the compare instead (in `deep` it replaces
   the investigator's draft of them). Write the interview record, every row answered, to
   `<run-dir>/interview.md`; it is frozen (hashed, given unchanged to both competitors). Run
   `python3 harness/core/scripts/compare.py design --task <task> --input <run-dir>/interview.md
   --checkout . --mode standard|deep` (the depth mode). Exit 2 is bad input or configuration and nothing
   was launched: an empty answer in the interview (fill it), a run that was not initialized
   (step 0) or an unusable overlay or model profile (fix it); then run it again. A stopped
   compare continues with `--resume <id>`. Show the user the arbiter's output verbatim
   (`<run-dir>/compare/<id>/comparison.md`) and the verdict in `judgement.json`: the ranking or
   tie, failed mandatory requirements (such a memo cannot win), and, when they hold, `arbiter
   unstable`, `capability-unequal` or an excluded memo. The user picks one approach memo or asks
   for a merge. You write the merge from both memos; the merge gets one review (the other vendor
   on `critical-review`) before it becomes the basis. Then one spec, as in step 6.
6. Write `docs/superpowers/specs/YYYY-MM-DD-<topic>.md`: requirements table, the section of
   every project row, chosen design, `## Placement` (below), contracts and error behaviour,
   validation, then ordered TDD units with dependencies, observable acceptance criteria and the
   paths each unit may change (globs), followed by the Taste gates. Order units so the first end-to-end slice lands early.
   There is no separate plan.
7. In `deep` mode, give the written spec to the other vendor for its second opinion first and
   fold in what stands. Obtain approval of unresolved material design choices before
   implementation. Existing
   approval covers the same design transcribed into a spec; record its source rather than
   requesting it again. A changed scope or new consequential choice requires a new answer.
   An unanswered approval request never becomes approval through elapsed time.
8. Hand the approved specification to `pharos-dev`.

## Placement

Every spec carries `## Placement`: for each new or moved piece — module, class, file, directory,
script, table — where it goes and why: its layer, its module and its file, and the existing code
it sits beside or extends. Draft it from the project's architecture map: the overlay's
`architecture.md` (the map in prose) and `architecture.json` (layers and what each may depend on,
where each kind of file lives, naming), else the project's map (AGENTS.md) and the code's own
shape. A piece the map has no place for is a design question for the user, not a new directory
by default. The unit paths follow Placement, the `architecture-reviewer` checks the diff against
it at every Tier 2–3 milestone, and a deviation is a finding unless the spec was amended — so
amend it when the placement changes during implementation. A harness-only or docs-only spec
writes "not applicable: <reason>".

```markdown
| Piece | Layer | Module / file | Why here |
|-------|-------|---------------|----------|
| export of the list as CSV | api | `app/routes/export.py`, next to the list route | same query and auth as the list; the formatter is shared with `app/csv.py` |
```

## Sources and output

Each answer is grounded in `user: <answer or earlier approved proposal>`,
`user: не важно` when the user explicitly leaves that item open, or `repo: <rule/path>` when an
existing invariant determines it. An unseen default is not a requirement: present it as a
recommended choice. A harness-only or otherwise irrelevant item may say "not applicable" with
its scope-based reason and source.

```markdown
## Requirements interview
| # | Item | Question | Answer | Source |
|---|------|----------|--------|--------|
| 1 | Goal and user | … | … | user: <earlier answer> / repo: <rule> |

## <section of each project row>
<drafting role's draft, updated with decisions; or not applicable: reason>

## Placement
<each new or moved piece: layer, module, file, why; or not applicable: reason>

## Taste gates
| Gate | After unit | What the user sees (evidence) | Decision asked | Blocks |
|------|-----------|-------------------------------|----------------|--------|
| G2 | 3 | the new list screen with demo data, both themes; curl of its endpoint | layout and filters right? | units depending on 3 |
```

Never omit these sections. An unresolved independent review is recorded separately from the
requirements themselves, with the capability needed to finish it.

## Red flags

| Thought | Reality |
|---|---|
| "Time pressure lets us drop a row" | Group questions within the runtime's limit; cover every row, project rows included. |
| "The default is obvious" | Cite the rule or ask the remaining material decision. |
| "A project row is technical, I'll decide" | A project asks a row on every feature because its answer affects users' rights or data; use established decisions or ask. |
| "We need approval again because the runtime changed" | Authorization persists across runtimes and sessions. |
| "The skill itself is the approval" | Record actual user authorization; a timeout is not an answer. |

## Harness feedback

Offer feedback when a harness script errors or contradicts its docs; a harness rule forces a
workaround or manual step; a session-built tool belongs in the harness; the user corrects a
profile, tier or gate decision; or the user complains about the harness process. Check the
queue first: `python3 harness/core/scripts/feedback.py list --run-dir <run> --offerable`
(or omit `--run-dir` when there is no run). Draft each distinct issue once with `draft --kind
bug|friction|feature --title "<drafted title>" [--from-file <harness-only evidence>]` using the
same queue selector. At a natural pause after the unit, gate or turn, use `offer <slug>` to
record and ask exactly: **Offer harness feedback “<drafted title>” for your review?** Never
interrupt a running decision. The user decides; reuse their answer. A refusal is `decline
<slug>` in that queue, so it is never offered again; `offer` remembers earlier offers too.

The repository `pharosone/dev-harness` is public. Include only minimal harness reproductions
and sanitized evidence; no product data. `send <slug>` previews the final sanitized text and
checks duplicates before creating anything. Show candidate issue links; only use
`--not-a-duplicate` when the user confirms they are distinct. Publication still needs approval.
The user must see that text before approval;
`--yes` is only for their explicit go for that exact text, never an agent's default. If local
vendored-core edits implement the fix, offer the printed `harness_sync.py propose
--feedback-file <draft>` command instead; it prepares a PR with the reviewed draft attached.
Review its code and body before the user authorizes publishing. `--issue` explicitly chooses
an issue instead. Missing `gh`/auth prints a prefilled browser URL; it stays unsent until
publication is verified. At a gate and run end, show `feedback.py list --run-dir <run>
--summary` in one line: **“2 harness feedback drafts waiting: <title>; <title>”**. Declined and
sent drafts remain recorded, without counting as waiting.
