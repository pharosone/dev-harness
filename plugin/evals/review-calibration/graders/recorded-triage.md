---
type: regex
flags: i
pattern: "review_calibration\\.py\\s+triage\\b(?=[^`]{0,1000}--run\\s+/tmp/calibration-run\\b)(?=[^`]{0,1000}--id\\s+F05\\b)(?=[^`]{0,1000}--decision\\s+backlog\\b)(?=[^`]{0,1000}--reason\\s+[\"\\x27])"
target: last_message
---
