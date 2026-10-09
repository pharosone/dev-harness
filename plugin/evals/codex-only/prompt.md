---
name: codex-only
description: The harness runs on Codex alone when Claude is unavailable, within owner-session limits; recorded need — the user must be able to run everything on Codex.
runs: 1
max_turns: 4
expected_outcome: Codex orchestrates on GPT-6.1 Sol (not Astra), with a gate at every milestone, a per-session response cap and single-vendor review.
---
Load the pharos-dev skill and answer from it. Claude is unavailable for the rest of the week and only Codex
is left. Can the next feature run on Codex alone, and what changes in how it runs? Under 100 words.
