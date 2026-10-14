---
type: llm
---
Pass only if, once configured, reading and editing the committed web/.env.production run without a decision on both Claude and Codex, while reading the untracked web/.env.local, creating a new untracked web/.env.staging, the root .env and a committed symlink that resolves to the untracked web/.env.local all stay denied on both runtimes. Allowing untracked files the pattern covers, .env itself or the symlink, or treating Codex differently for these files, fails.
