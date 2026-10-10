---
type: regex
flags: i
pattern: "never[\\s\\S]{0,60}(offer|ask)|not[\\s\\S]{0,60}(offer|ask|re-offer)|no[\\s\\S]{0,40}(repeat|re-offer|new offer)|skip[\\s\\S]{0,40}(offer|question)|already offered|declined.*(persist|retain|record)|won.t[\\s\\S]{0,40}(offer|ask)"
target: last_message
---
