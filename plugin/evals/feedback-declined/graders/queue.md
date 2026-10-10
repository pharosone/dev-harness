---
type: regex
flags: i
pattern: "feedback\\.py[\\s\\S]{0,80}\\blist\\b[\\s\\S]{0,80}--run-dir[\\s\\S]{0,40}discovery-run|--run-dir[\\s\\S]{0,40}discovery-run[\\s\\S]{0,80}\\blist\\b"
target: last_message
---
Inspect the same durable feedback queue before deciding whether a differently worded complaint is new.
