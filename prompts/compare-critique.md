# Critique: break an investigation report

Two investigators answered the same question independently, in fresh contexts. Below are the
investigation brief they were given (the question and what is known) and ONE of the two
reports. You wrote neither. Your job is adversarial: try to break
the report's conclusion, not to summarise it or to write a third investigation.

The brief may also carry a section headed "the other investigation's answer, for comparison":
the other report's `**Answer:**` line and nothing else of it. Use it only to decide whether the
two answers conflict; critique the report under review, not that line.

Report only: never edit, create or delete files and never run commands that change anything.
Open files of the checkout, or run read-only commands, only to check a specific claim the
report makes. Do not look for the other report or any file under the run's `compare/`
directory.

## What to look for

- **Evidence that does not hold** — an observation that does not show what the report says it
  shows, a citation (`path:line`, command, query) that is wrong or missing, a reproduction that
  was not actually run, an argument presented as an observation.
- **Hypotheses not ruled out** — competing explanations or options the report dismissed without
  a discriminating observation, or never considered.
- **Cheaper explanations** — a simpler mechanism that fits the same evidence.
- **Stretched confidence** — a stated confidence the evidence does not carry.

For every claim you dispute, name the cheapest observation that would settle it: a command, a
query, a minimal reproduction, a number to measure. Prefer one that splits the two readings.

## Output

Write your critique first, claim by claim, citing the report's text and what you checked. Then
end with exactly one fenced JSON block, nothing after it:

```json
{"agreed": ["the retry loop has no backoff (src/retry.py:40, confirmed)"],
 "disputed": [{"claim": "the timeout causes the failure",
               "evidence_for": "the report's log shows a timeout before the failure",
               "evidence_against": "the same timeout appears in passing runs (ci log 812)"}],
 "settles": [{"claim": "the timeout causes the failure",
              "observation": "rerun the failing test with the timeout doubled"}],
 "answer_disputed": false,
 "answers_conflict": false}
```

- `agreed`: the report's claims that hold up under your check, one sentence each.
- `disputed`: the report's claims you dispute, each with the report's evidence for it
  (`evidence_for`) and yours against it (`evidence_against`).
- `settles`: for each disputed claim (the same `claim` text), the cheapest observation that
  would settle it; the cheapest first when you name several.
- `answer_disputed`: `true` when you dispute the report's **Answer** itself, not only parts of
  its support; `false` otherwise.
- `answers_conflict`: `true` when the report's **Answer** and the other investigation's answer
  cannot both hold (one says enable retries, the other disable them), even when each looks
  supported on its own; `false` when they agree, are compatible, or the brief carries no
  other answer. A conflict counts as a disputed answer.
