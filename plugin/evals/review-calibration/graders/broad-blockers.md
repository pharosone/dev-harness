---
type: regex
flags: i
pattern: "^(?=[\\s\\S]*F01\\s*:\\s*block\\b)(?=[\\s\\S]*F02\\s*:\\s*block\\b)(?=[\\s\\S]*F03\\s*:\\s*block\\b)(?=[\\s\\S]*F04\\s*:\\s*block\\b)(?=[\\s\\S]*F09\\s*:\\s*block\\b)[\\s\\S]*$"
target: last_message
---
