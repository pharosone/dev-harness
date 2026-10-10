---
type: regex
flags: i
pattern: "compare[^\\n]{0,60}\\(?recommended|recommend[^\\n]{0,60}compare|compare[^\\n]{0,60}\u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434|compare[`*]*[ \\t]*\\r?\\n(?:[ \\t]*[-*][^\\n]*\\r?\\n){0,2}[ \\t]*[-*][ \\t]+[`*]*design[`*]*[ \\t]*\\([`*]*recommended\\b"
target: last_message
---
