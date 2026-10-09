---
name: pharos-investigate
description: "Use when something must be understood before anyone acts: a bug whose cause is not known yet (intermittent, data-dependent, only in one environment, a fix that did not hold), research of data or options for a decision, or understanding how unfamiliar code really works. On \"разберись\", \"почему\", \"найди причину\", \"исследуй\", \"что выбрать\", \"investigate\", \"root cause\"."
---

# pharos-investigate — understand before acting

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/pharos-investigate.md` (this skill's project specifics) before following
this procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it.

**Goal:** an answer someone can act on — the root cause of a bug with its mechanism, the
numbers and trade-offs a decision needs, or a true picture of how code behaves — with its
evidence and an honest confidence. Not a fix, not a spec: those follow from it.

## Start: the mode question

Ask once, with the question tool of the runtime adapter, together with anything else missing
(the question, the environment, what may be touched). The recommended option goes first and
says why:

- **`standard`** — you investigate yourself on the session's model. Fits when the cause or the
  answer is likely one or two observations away.
- **`deep`** — a fresh `investigator` on the `deep-reasoning` profile (Opus `xhigh` / Astra
  `xhigh`) does one deep pass, and the other vendor gives a second opinion on its report before
  it reaches the user. Recommend it when a wrong answer is expensive or the obvious answer
  already failed: the cause is not established after a first look; the failure is intermittent,
  concurrent, data- or environment-dependent; a previous fix did not hold; the decision is
  costly to reverse or needs data analysed; the code spans unfamiliar layers.

Write the recommendation as "`deep` (Recommended) — <the signal you saw>" or the same for
`standard`. Record the answer, model and effort in `run.md`
(`python3 harness/core/scripts/task_state.py init <task>`).

## Run it

1. **Frame:** the question in one sentence and what observation settles it (the investigator
   role defines the frame, the hypotheses and the report; follow it in `standard` mode too).
2. **Evidence:** reproduce, measure, query, bisect, instrument — in a scratch directory or a
   worktree (`python3 harness/core/scripts/worktree.py add <name>`), never by editing the
   checkout under investigation. Production data and systems only read-only and only as the
   user allowed; no secrets in briefs, reports or logs; never deploy.
3. **`deep`:** brief the `investigator` with the question, what is known with sources, the
   environment and its limits, and the report path `<run-dir>/investigation.md`. Then the
   second opinion: the other vendor's `investigator` gets the question and the report and is
   asked to break its conclusion — evidence that does not hold, a hypothesis not ruled out, a
   cheaper explanation. Claude-only or Codex-only mode: a fresh context of the same vendor,
   recorded as single-vendor. Reconcile: a challenge that stands goes back to the investigator
   once, or into the report's Unknowns.
4. **Report to the user:** the answer, the confidence and what would change it, the evidence in
   short, the recommendation and the open unknowns. A confidence below high says what it would
   cost to raise it.

## Hand-off

- A bug: the reproduction becomes the failing test of a `pharos-dev` fix unit.
- A decision: the options with their numbers, recommended first, as a question to the user;
  the answer is recorded before dependent work.
- A feature or redesign: `pharos-discover`, with the report as the design's evidence.

## Stop and ask

- The evidence needs access the user has not granted (production, paid compute, private data).
- Two passes left the answer at low confidence: present what is known and the cost of the next
  step instead of a third pass.
