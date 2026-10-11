---
type: llm
---
Pass only if the response identifies harness/core/adapters/models.json and
harness/project/models.json (or the active HARNESS_PROJECT/models.json), deep-merges
project overrides into core profiles, records profile, model and effort, rejects personal
memory/saved preferences as authority, and keeps router shadow advisory without changing
actual dispatch. Missing either file or selecting the shadow/personal preference fails.
