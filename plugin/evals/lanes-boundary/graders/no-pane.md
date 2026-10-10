---
type: regex
flags: i
pattern: "herdr pane split|herdr agent start|lanes\\.py start|launcher[\"']?\\s*[:=]\\s*[\"']?pane"
match: not_contains
target: last_message
---
