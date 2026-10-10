---
name: manual-test-scope
description: Purpose-based coverage for a skill affected by shared instruction references.
runs: 1
max_turns: 4
expected_outcome: Applies the skill-specific evidence, artifact and workflow contract.
---
Load the manual-test-plan skill. A docs-only Tier 0 PR is ready, while a separate Tier 2 UI PR includes a sign-in the agent cannot complete. State which needs a plan, what the first relies on, and the artifact and interaction rules for the human-only residual steps. Do not publish a comment. Under 140 words.

Shadow-mode regression context: The UI PR also displays an optional shadow model recommendation beside the actual static tuple. Classifier experiment C is unrun. Include one bounded residual check for that display; do not treat model benchmarking as a manual UI test.
