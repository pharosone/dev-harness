---
type: regex
flags: i
pattern: "Monitor[\\s\\S]{0,300}(re.?arm|restart|expir)|(?:re.?arm|restart|expir)[\\s\\S]{0,300}Monitor"
target: last_message
---
