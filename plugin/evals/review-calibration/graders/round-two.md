---
type: regex
flags: i
pattern: "^(?=[\\s\\S]*R01\\s*:\\s*backlog\\b)(?=[\\s\\S]*R02\\s*:\\s*block\\b)(?=[\\s\\S]*R03\\s*:\\s*block\\b)[\\s\\S]*$"
target: last_message
---
