# Approach memo

You are one of two independent competitors. Each of you gets this same brief and the same
frozen interview record below, in a fresh context, and writes an approach memo for it. An
arbiter who wrote neither memo compares the two against the rubric at the end of this brief.
You will not see the other memo, and you must not look for it: read only the source checkout
and the interview record, and never open other files under the run's `compare/` directory.

## What to write

A memo of one to three pages, in Markdown, with these sections:

1. **Design** — the approach in a few paragraphs: the parts, who owns what, and the data flow.
   Name the alternatives you rejected and why.
2. **Contracts** — the interfaces other code or people rely on: commands, file formats, events,
   exit codes, schemas. Exact names and fields, not prose.
3. **Failure handling** — timeouts, partial writes, concurrency, retries, what the user sees
   when something breaks.
4. **Cost** — invocations, time and the work to build it, in their own units; unknowns stay
   unknown, never guessed.
5. **Risks** — what could make this approach wrong, and the cheapest observation that would
   tell.
6. **Evidence** — what in the checkout or the record supports each claim: `path:line`, a command
   you ran and its output, or a row of the interview record. Mark each unverified claim as such.

Cover every row of the requirements table. Say so explicitly where a requirement cannot be met
by your approach; do not leave it silent.

## How to work

- The source checkout is read-only for you: never edit, create, stage or delete files in it.
- Your writable scratch directory is the directory that holds your report. Use it for
  experiments; nothing in it except the memo is graded.
- Write your memo to the report path given at the end, and write it as you go: save a first
  outline early and extend it, so a timeout still leaves a usable memo.
- Do not ask questions. Where the record is unclear, state the assumption you made.

## Rubric the arbiter uses

Requirement coverage, grounding in evidence, alternatives and uncertainty, feasibility and
cost, actionability. A requirement marked mandatory that the memo fails cannot be outweighed by
strength elsewhere.
