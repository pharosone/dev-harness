---
name: deep-mode-offer
description: A non-obvious bug gets the depth question with deep recommended; recorded gap — deep reasoning was never offered before work started.
runs: 1
max_turns: 3
expected_outcome: Asks standard or deep, recommends deep with the reason (intermittent, a fix that did not hold); does not start fixing.
---
Load the pharos-investigate skill. The user writes: "After the last release a dialog sometimes
loses its flagged status; it happens maybe once a day, and the fix we shipped last week did not
help." Following the skill, write only your first message to the user. Under 120 words.
