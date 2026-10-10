---
type: regex
flags: i
pattern: "(?:\\bnever|\\b(?:do\\s+)?not|\\b(?:won|don)['’]t)\\s+(?:(?:be|ever|again)\\s+){0,3}(?:re[- ]?)?(?:offer(?:ed|ing)?|ask(?:ed|ing)?)\\b|\\bno\\s+(?:repeat|re-offer|new offer|further(?: feedback)? offers?)\\b|\\bskip\\s+(?:(?:the|this)\\s+)?(?:feedback\\s+)?(?:offer|question)\\b|\\balready offered\\b|\\bdeclined\\b[^\\n.]{0,40}\\b(?:persist\\w*|retain\\w*|record\\w*)"
target: last_message
---
