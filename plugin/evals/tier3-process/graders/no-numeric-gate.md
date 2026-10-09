---
type: regex
flags: i
pattern: "(score|ux)[^\\n]{0,20}(≥|>=)\\s*90|90/90"
match: not_contains
target: last_message
---
