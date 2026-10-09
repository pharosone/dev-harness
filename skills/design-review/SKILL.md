---
name: design-review
description: "Use when a PR, branch or diff in this repository needs an architecture pass — leaking abstractions, a layer owning what another should, what became public, drift between sibling implementations. On \"design review\", \"architecture review\", \"ревью дизайна\", \"архитектурное ревью\", \"проверь абстракции\"."
---

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/design-review.md` (this skill's project specifics) before following
this procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it. Resolve task artifacts with `python3 harness/core/scripts/task_state.py locate
<task>`; use the returned absolute directory, including for an existing legacy run. `<run-dir>`
below means that resolved directory. Reuse existing user authorization; publish comments or
reviews only when the user explicitly requested it or explicitly invoked a skill authorizing
that action.

# Design review

An architect's pass over a change: does every layer still own exactly what it owned, does the
new code satisfy its abstraction without a workaround, is what became public needed, and which
seams will cost the most a year out. Bugs belong to the independent QA/code review, performance to
`perf-analyst`, security to `security-auditor` — out of scope here.

The deliverable is one short Markdown report: a verdict, a 0–100 design score, and a ranked list
of findings with evidence on both sides of each boundary.

## Picture first, diff last

A finding about a boundary needs both sides of it, and the diff shows one. Read the diff last.

1. **Pin the exact head.** For a PR: `gh pr view <n> --json baseRefName,baseRefOid,headRefName,headRefOid`;
   read from a detached worktree on the head SHA in the scratchpad
   (`git worktree add --detach <scratch>/pr<n> <headOid>`), never `gh pr checkout` in the main
   checkout. Remove only worktrees you created.
2. **Read the decisions already taken:** the spec under `docs/superpowers/specs/`, the PR body,
   earlier reviews (`gh pr view <n> --json comments,reviews`). A rejected idea is not a finding;
   a deviation from a taken decision is, and so is a taken decision's unstated consequence —
   name the decision and propose the smallest change that keeps its goal, never a revert.
3. **Name the layer stack** the change lives in, from the project's map (AGENTS.md) and the
   code — entry points, domain logic, data access, schema, side channels (events, storage, auth,
   external services), client layers and their wire types, whatever this project has — and read
   each layer's contract in full.
4. **Build the ownership grid** before judging: one row per aspect (state, status writers,
   errors, scope predicate, auth mode, side effects, what each layer swallows vs passes up),
   one column per layer or sibling implementation, every cell a file:line. About ten rows.
5. **Walk each public operation end to end** through every layer and check where consumers get
   their answers; grep the consumers. A contract only a test harness implements is a finding.
6. **Check the public surface:** every new exported symbol on either side of the stack — grep
   for consumers outside its package or module; zero consumers is a leak.
7. **Now the diff**, production files first, `--stat` including non-code files (CI, build and
   deploy files): unrelated changes ride in large PRs.
8. **Verify every claim** by reading or running before writing it.

## Output

One Markdown file — `<run-dir>/design-review-<n>.md` unless the caller names a path —
in English, so it can post as a PR review as is: plain English for a PR review, no HOW-prose or
session jargon, every fact kept. Posting is a separate, explicit step: one
`gh api repos/{owner}/{repo}/pulls/{pr}/reviews` call with `event: COMMENT`, the non-finding
parts as `body`, one inline `comments` entry per finding.

Exactly these parts, in this order:

1. `# Design review`
2. `**Verdict:** <V> · **Design score:** NN/100`, then two or three sentences: what the change
   is, whether the abstraction holds, the theme of the drift.
3. **Ownership** — the contested rows of the grid only, opened by one gloss line: *each row is
   one concern, each cell the layer that owns it (file:line); a row whose cells disagree is a
   seam, and the seam is a finding.*
4. `## Findings` → `### Major` then `### Minor`, at most seven in all, each a table
   `# | Finding | Where | Fix`. Finding = one plain sentence: what is wrong and what it costs.
   Where = file:line on both sides plus the consumer grep. Fix = a change the author could type.
5. **Noted / Do not touch** — terse bullets: below the bar, and decisions the review confirms.

## Verdict and score

| Verdict | Score | Means |
|---|---|---|
| **Good** | 85–100 | The abstraction holds; ship as is. |
| **Minor drift** | 70–84 | Sound core; small seams slightly off, fixable any time. |
| **Major drift** | 45–69 | Sound direction, but seams the next layer will push on need a home first. |
| **Bad** | 0–44 | The abstraction does not hold. |

Anchor the score to the worst finding and count a shared root once. Score the change, not the
codebase.

## What clears the bar

A finding is a seam this change creates or widens, with evidence on both sides, that costs
something now: the same concern owned or decided differently by two layers or sibling
implementations, a contract stated in the spec with no path through the layers, a meaning that
changes across a boundary, public surface nobody consumes, an unrelated change riding along.
Which seams matter depends on the project's architecture; the Project specifics name the ones it
has paid for before. Not findings: style, length, naming inside one file, missing comments,
test strength (QA's), performance inside one function (perf's).

## Gotchas

- A cost that only lands with a hypothetical next layer is Minor at most; rank by what the code
  in this PR pays today.
- Existing match/switch arms give an enum value its meaning: grep consumers before accepting a
  new producer of an existing status or error.
- A finding with evidence on one side of the boundary only, or only in test files, is not done.
- A Finding cell is one sentence without metaphors or coined shorthand; a reader who never saw
  the session must be able to act on the row alone.
