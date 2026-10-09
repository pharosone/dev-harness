---
name: address-gh-review
description: "Use when a reviewer left comments on a GitHub PR of this repository and they need working through (\"I added comments\", \"address the review\", \"обработай комменты\", \"ответь на ревью\")."
---

Read `harness/core/README.md`, the active runtime adapter and, when present,
`harness/project/skills/address-gh-review.md` (this skill's project specifics) before following
this procedure. `harness/project` in this skill means the overlay in use: `$HARNESS_PROJECT` when set; `python3 harness/core/scripts/project.py overlay` prints it. Resolve task artifacts with `python3 harness/core/scripts/task_state.py locate
<task>`; use the returned absolute directory, including for an existing legacy run. `<run-dir>`
below means that resolved directory. Reuse existing user authorization; publish comments or
reviews only when the user explicitly requested it or explicitly invoked a skill authorizing
that action.

# Address a GitHub PR review

The unit of work is the whole review round, not one comment: one push and one reply per thread,
after the full pipeline is green again. A reply pointing at a half-tested push is worse than no
reply. Cap: one round per invocation; stop and report after it.

## 1. Collect

`gh pr view --comments` misses inline threads. Read all three sources (`gh api` fills in
`{owner}/{repo}` from the current checkout):

```bash
gh api repos/{owner}/{repo}/pulls/<n>/comments --paginate \
  --jq '.[] | {id, user: .user.login, path, line, original_commit_id, in_reply_to_id, body}'
gh api repos/{owner}/{repo}/pulls/<n>/reviews --jq '.[] | {id, user: .user.login, state, body}'
gh api repos/{owner}/{repo}/issues/<n>/comments --jq '.[] | {id, user: .user.login, body}'
```

A thread root has `in_reply_to_id: null`; skip threads that already carry the author's reply.
`line` refers to `original_commit_id`: read `git show <original_commit_id>:<path>` before mapping
it to the current head.

## 2. Triage and plan

Read the code under each thread, then one verdict: take, reject (with the reason), investigate.

- "Verify with <agent>" (perf, security, design) → dispatch that agent first, in the background,
  with file:line anchors and the exact question; it is the long pole.
- A "how can this happen" question is a design challenge: answer with facts; when the answer is
  "it cannot", the fix is fail-fast, not a tolerated branch.
- "Remember it as a gotcha" / "add a rule" → `harness/project/rules/*.md` for a path-scoped
  rule, the project's map (AGENTS.md) for a repo-wide one, a QA gotchas file for a QA check
  (`harness/project/references/qa-gotchas.md`; a check that holds for any stack is proposed to
  the core instead, never edited into `harness/core` in place) — and apply it to this PR's code.
  Log the harness change in `harness/project/HARNESS_LOG.md`.
- "Do X everywhere" means the whole repo, in its own commit.

Write the plan to `<run-dir>/review-round-<k>.md`: one row per thread — comment id,
full comment text, verdict, change, the test that proves it.

## 3. Fix

New commits on the branch (conventional messages), or `git commit --fixup <sha>` +
`GIT_SEQUENCE_EDITOR=true git rebase -i --autosquash <base>` when the user prefers a clean
history. Nothing leaves the machine yet.

## 4. Re-run the whole pipeline

Not only the tests the fixes touch: every gate from `submit-pr` step 1, the panel lenses on the
round's delta (triage their findings like the reviewer's), new screenshots from the
`ui-design-verifier` when the change reaches the screen, and a re-check of the manual test plan and the PR body against the new diff.

## 5. Push, then reply

Push (`--force-with-lease` after a rebase), update the PR body, post the round's verdict comment
(Taken / Rejected / Deferred). When CI is green, reply in every thread:

```bash
gh api -X POST repos/{owner}/{repo}/pulls/<n>/comments/<id>/replies -F body=@reply.md
```

- Verdict first on its own line ("Done —", "Fixed —", "Kept —"), then the evidence: commit,
  number, or file:line. Plain English, every fact kept.
- Never resolve a thread; the reviewer resolves it after reading.
- Reply to the author's own self-review threads too.

## 6. Report

One line per thread with its verdict, then CI state, new screenshots and the PR link.
