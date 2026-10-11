---
type: llm
---
Pass only if the answer reuses the existing dedicated long-lived test Postgres service with unique logical databases, uses a migrated template or immutable migrated snapshot keyed by migration-content hash plus relevant setup inputs, and rejects per-test containers absent a concrete isolation reason. Single-connection rollback is insufficient for writes committed through multiple connections. It must preserve real-service contract coverage: fakes do not prove actual database/provider or model-quality behavior.
