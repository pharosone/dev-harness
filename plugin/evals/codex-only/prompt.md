---
name: codex-only
description: The harness runs on Codex alone when Claude is unavailable, within owner-session limits; recorded need — the user must be able to run everything on Codex.
runs: 1
max_turns: 4
expected_outcome: Codex orchestrates on GPT-6.1 Sol (not Astra), with a gate at every milestone, a per-session response cap and single-vendor review.
---
Load pharos-dev if available and answer from its instructions. If unavailable, say so and use
general judgment without searching for the missing skill. Do not read files or execute commands.
Claude is unavailable for the rest of the week and only Codex is left. Can the next feature run on Codex alone, and what changes in how it runs? Under 100 words.

Shadow-mode regression context: The optional router is in shadow mode with an unvalidated Haiku classifier configured. Its advisory recommendation mentions a Claude tuple. Explain how this affects the Codex-only dispatch and whether Haiku may be called in this runtime mode.
