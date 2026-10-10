---
name: feedback-declined
description: A declined harness complaint is retained without repeated offers or automatic publication during discovery.
runs: 1
max_turns: 3
expected_outcome: Record decline, never re-offer that issue, continue discovery without publishing product data.
---
Load the pharos-discover skill. The user complained about harness model selection. You
already offered the feedback draft "Resolve models at dispatch", and the user replied:
"No, don't offer this again." No publication has been authorized. You encounter the same
problem again while drafting the spec, now described with a different proposed title.
The existing draft slug is `resolve-models-at-dispatch`; the durable run is
`/tmp/discovery-run`. Give the exact harness CLI commands to record the refusal and inspect
the queue before deciding whether the reworded complaint is new. State which recorded
states you compare, whether you ask the feedback question again, and how discovery proceeds.
Under 150 words.
