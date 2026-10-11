---
type: llm
---
Pass only if affected internal/store cases/package run first locally, full suites remain required in CI/pre-PR, and parallelism obeys actual host load/memory capacity rather than fixed slots or unbounded fan-out. Idle headroom starts immediately; targeted runs do not wait. Infrastructure changes must report isolation justification and measured wall-time/resource or container-start cost.
