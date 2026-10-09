# Harness log

Every change to this repository's harness — the overlay in `harness/project/`, the rendered
`.claude/`, `.codex/` and `.agents/` files, a core update — gets one row: what changed, where,
and the failure that caused it. Changes to the core itself are logged in the core's
`CHANGELOG.md` and proposed upstream with `harness_sync.py propose`.

| Date | What | Where | Failure that caused it |
|---|---|---|---|
