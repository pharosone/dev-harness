---
name: lanes-boundary
description: A short same-vendor read-only review is a subagent, not a pane; recorded failure — herdr panes opened for every worker and crowded the user's screen.
runs: 1
max_turns: 4
expected_outcome: Names lanes.py decide and starts the worker as a subagent; no pane is opened.
---
We orchestrate in Claude Code with the pharos-dev skill, and HERDR_ENV=1 is set. Next is a
read-only review of a small diff by a Claude reviewer: about five minutes, report only, no
questions for the user, and the next step needs its result. How do you start this worker —
subagent, headless or a herdr pane — and which command decides? Under 60 words.
