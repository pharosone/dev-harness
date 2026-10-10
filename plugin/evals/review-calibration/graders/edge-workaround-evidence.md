---
type: regex
flags: i
pattern: "^(?=[\\s\\S]*F05\\s*:\\s*backlog\\b)(?=[\\s\\S]*F06\\s*:\\s*block\\b)(?=[\\s\\S]*F07\\s*:\\s*backlog\\b)(?=[\\s\\S]*F08\\s*:\\s*backlog\\b)(?=[\\s\\S]*F10\\s*:\\s*backlog\\b)[\\s\\S]*$"
target: last_message
---
