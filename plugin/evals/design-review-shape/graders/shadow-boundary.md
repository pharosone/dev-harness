---
type: regex
flags: i
pattern: "^(?=[\\s\\S]*static[^\\n]{0,100}(?:balance|selector))(?=[\\s\\S]*shadow[^\\n]{0,100}(?:advisory|only observe|must never|must not|never decid|no[^\\n]{0,30}dispatch))(?=[\\s\\S]*(?:consumer|file:line))[\\s\\S]*$"
target: last_message
---
