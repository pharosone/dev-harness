---
type: regex
flags: i
pattern: "astra[^.\\n]{0,40}(orchestrat|owner|coordinat)"
match: not_contains
target: last_message
---
