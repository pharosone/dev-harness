---
type: regex
flags: i
pattern: "\\b(security|perf(ormance)? lens|design review|qa-verifier) (pass|lens|review) (is )?(required|needed|runs)"
match: not_contains
target: last_message
---
