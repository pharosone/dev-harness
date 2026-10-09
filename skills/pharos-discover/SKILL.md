---
name: pharos-discover
description: "Use when a feature, endpoint, screen or behaviour change in this repository needs requirements, alternative approaches and an approved specification with TDD units; also when a spec lacks its Requirements interview, its Taste gates or a section a project interview row requires."
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

## How to run it

1. Read the project's map (AGENTS.md), the applicable `harness/project/rules/*.md` and the
   touched code. Inspect the current implementation before assuming a future contract already
   exists.
2. Map the conversation and repository rules to every row. Ask the depth mode (above) and
   unresolved Goal, Roles, Scope and UI first, using the question mechanism and batch limit supported by the runtime adapter.
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
6. Write `docs/superpowers/specs/YYYY-MM-DD-<topic>.md`: requirements table, the section of
   every project row, chosen design, contracts and error behaviour, validation, then ordered TDD
   units with dependencies, observable acceptance criteria and the paths each unit may change
   (globs), followed by the Taste gates. Order units so the first end-to-end slice lands early.
   There is no separate plan.
7. In `deep` mode, give the written spec to the other vendor for its second opinion first and
   fold in what stands. Obtain approval of unresolved material design choices before
   implementation. Existing
   approval covers the same design transcribed into a spec; record its source rather than
   requesting it again. A changed scope or new consequential choice requires a new answer.
   An unanswered approval request never becomes approval through elapsed time.
8. Hand the approved specification to `pharos-dev`.

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
