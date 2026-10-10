---
name: watch-react
description: Recorded failure — Codex lanes waited on questions while a live product repeated failed reads until the owner noticed.
runs: 1
max_turns: 4
expected_outcome: Arms the durable watcher through Monitor, re-arms on expiry, handles the resolved question and stops the product's retry loop in the same turn.
---
You orchestrate pharos-dev on Claude Code. You just recorded run.start for task pilot; the
Herdr dashboard is open. A Codex lane is waiting on a question whose answer is explicit in the
spec. The product's live worker has repeated the same failed read for 20 minutes; the overlay
names its own stop command as `python3 scripts/pilot_worker.py stop`. Another lane has written
its report. Which observer do you arm, how does it stay alive, and what do you do with these
conditions? Describe actions under 130 words; do not execute product or lane commands.
