---
name: frozen-test-amendments
description: Recorded run friction — format and fixture mistakes blocked implementers; additive tool lists required hand edits.
runs: 1
max_turns: 8
expected_outcome: Uses only verified amendment categories, initial brief authorization and durable first-green evidence.
---
Load pharos-dev if available. Read only its declared README/runtime-adapter references needed
to answer the frozen-test policy; those documents have not been preloaded. If the skill is
unavailable, say so and use general judgment without searching for it. Do not execute commands.
You coordinate the following policy decision. Five frozen
Python test edits are proposed: line wrapping and an unused import removal; changing
`self.expected = {"a": 1}` to `{"a": "1"}` in setUp before first green with the identical
assert `self.assertEqual(parse("a=1"), self.expected)` and saved red log
`ModuleNotFoundError: No module named 'tools'`; appending to EXPECTED, a named literal authorized by the original brief;
changing an assert's expected value; and replacing setup with self.skipTest. Explain which can refreeze automatically, the
exact brief declaration/flag, how the old baseline is preserved, and the command that records
the first green (are verify or an all-skipped OK log enough?). Would a related setup traceback
allow changing fixture return values used by assertions? Give one safe local setup repair
and its required evidence. Under 220 words; this is advice, not a request to edit or run tests.
