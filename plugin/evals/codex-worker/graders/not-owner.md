---
type: regex
flags: i
pattern: "\\byes\\b[^.\\n]{0,40}\\b(own|coordinat)|codex should (own|coordinat)|let codex (own|coordinat)"
match: not_contains
target: last_message
---
