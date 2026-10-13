---
type: llm
---
Pass only if the setting is a list of repository-relative patterns in the project.json overlay of the repository that holds the files, a separate core checkout's overlay exempts nothing there, no pattern can name .env itself, and changing the setting still needs the existing guard-configuration approval (Claude asks the user; Codex is denied). Describing owner authorization alone as changing the hook policy, or letting the core checkout's overlay grant the exception, fails.
