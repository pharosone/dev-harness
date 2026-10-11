---
name: pharos-investigate
description: "Use when something must be understood before anyone acts: a bug whose cause is not known yet (intermittent, data-dependent, only in one environment, a fix that did not hold), research of data or options for a decision, or understanding how unfamiliar code really works. On \"разберись\", \"почему\", \"найди причину\", \"исследуй\", \"что выбрать\", \"investigate\", \"root cause\"."
---

**Dispatch authority:** resolve every orchestrator/worker model and effort from
`harness/core/adapters/models.json` deep-merged with the active
`harness/project/models.json` (or `$HARNESS_PROJECT/models.json`). Record profile, model
and effort. Never select from personal memory or saved preferences; router shadow is
advisory and cannot replace the actual static profile choice. Lane progress goes to
`<report>.progress.md`; write final `<report>` only at the end. Requests use
`<run>/lanes/<work>.flag.md` or `tripwire.py flag`; never type into another pane.

# pharos-investigate — understand before acting

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/pharos-investigate.md` (this skill's project specifics) before following
this procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it.

**Goal:** an answer someone can act on — the root cause of a bug with its mechanism, the
numbers and trade-offs a decision needs, or a true picture of how code behaves — with its
evidence and an honest confidence. Not a fix, not a spec: those follow from it.

## Efficient testing authority

Apply `harness/core/rules/efficient-testing.md` to this workflow's tests and recommendations.
Choose in-process fakes first, then one shared long-lived test service with owned logical
isolation, then per-run services, then per-test containers only for a written correctness
reason. Reuse expensive setup with complete content-hash keys. Keep build/result/dependency/
image caches enabled; bypass only a named test/step for an input that its cache cannot see.
Run affected cases/packages first locally; the full suite remains required in CI/pre-PR.
Heavy gates/evals/reviews use `harness/core/scripts/load_guard.py` for adaptive load/memory
admission, never fixed slots; targeted runs
and nested admitted work do not wait. Infrastructure changes name the isolation boundary,
cleanup and measured cold/warm wall/resource cost. Never replace required real paired model
measurements or real-service boundary coverage with a fake.

## Start: the mode question

Ask the depth mode and compare once, with the question tool of the runtime adapter, together
with anything else missing (the question, the environment, what may be touched). The
recommended option goes first and says why:

- **`standard`** — you investigate yourself on the session's model. Fits when the cause or the
  answer is likely one or two observations away.
- **`deep`** — a fresh `investigator` on the `deep-reasoning` profile (Opus `xhigh` / Astra
  `xhigh`) does one deep pass, and the other vendor gives a second opinion on its report before
  it reaches the user. Recommend it when a wrong answer is expensive or the obvious answer
  already failed: the cause is not established after a first look; the failure is intermittent,
  concurrent, data- or environment-dependent; a previous fix did not hold; the decision is
  costly to reverse or needs data analysed; the code spans unfamiliar layers.

Write the recommendation as "`deep` (Recommended) — <the signal you saw>" or the same for
`standard`.

Ask `compare` in the same question; the default is `off`:

- **`off`** (default) — the investigation as described here.
- **`investigation`** — two independent investigators, one per vendor, answer the same frozen
  brief without seeing each other's report; then each vendor critiques the other's report, and
  the two critiques become a disagreement map. Recommend it when the decision is costly to
  reverse, there is conflicting evidence, or a deep pass already left low confidence:
  "`compare=investigation` (Recommended) — <the signal>". Without that signal recommend `off`.

`compare=off` leaves the flow unchanged: `deep` is one investigator and a second opinion, as
before. Record the answers, model and effort in `run.md`
(`python3 harness/core/scripts/task_state.py init <task>`) and as the run's first event:
`task_state.py event <task> run.start --data '{"workflow": "investigate", "depth": "<mode>",
"compare": "off|investigation", "runtime_mode": "<mode>"}'`.

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
   **`compare=investigation`** replaces step 3. Write the brief to `<run-dir>/brief.md`: a
   `## Question` section, what is known with sources, the environment and its limits; it is
   frozen (hashed, given unchanged to both). Run
   `python3 harness/core/scripts/compare.py investigation --task <task> --input
   <run-dir>/brief.md --checkout .`; exit 2 is bad input and nothing was launched; a stopped
   compare continues with `--resume <id>`. It writes two reports, a critique of each by the
   other vendor, and in `<run-dir>/compare/<id>/` the disagreement map (`disagreements.md`:
   claims agreed, claims in dispute with both sides' evidence, the cheapest observation that
   settles each) and `comparison.md` (both critiques).
   Show the user the map and the critiques verbatim. A disputed answer means low confidence
   until the settling observation is made; a missing report or critique is said aloud.
4. **Report to the user:** the answer, the confidence and what would change it, the evidence in
   short, the recommendation and the open unknowns (with compare: the disputed claims and their
   settling observations). A confidence below high says what it would cost to raise it.

## Hand-off

- A bug: the reproduction becomes the failing test of a `pharos-dev` fix unit.
- A decision: the options with their numbers, recommended first, as a question to the user;
  the answer is recorded before dependent work.
- A feature or redesign: `pharos-discover`, with the report as the design's evidence.

## Stop and ask

- The evidence needs access the user has not granted (production, paid compute, private data).
- Two passes left the answer at low confidence: present what is known and the cost of the next
  step instead of a third pass.
