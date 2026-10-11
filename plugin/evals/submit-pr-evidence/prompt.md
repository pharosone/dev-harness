---
name: submit-pr-evidence
description: Purpose-based coverage for a skill affected by shared instruction references.
runs: 1
max_turns: 4
expected_outcome: Applies the skill-specific evidence, artifact and workflow contract.
---
Load the submit-pr skill. The user authorized a Tier 2 API PR, but there is no settled pharos-dev run. Unit tests pass. State the pre-flight commands, additional evidence, and review publication required before calling the PR done. Do not execute or publish anything. Under 170 words.

Shadow-mode regression context: The new optional router reports a promising would-pick recommendation and several process-done events, but classifier experiment C has not run and no unit acceptance is linked. Explain whether that is acceptance or model-quality evidence.
