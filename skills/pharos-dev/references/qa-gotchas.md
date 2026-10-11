# QA gotchas

Checks behind the `qa-verifier` pass, one section per area a diff can touch. Each entry is a way
a green report can cover broken code. Read only the sections that match the diff. These are the
stack-independent ones; the project keeps its stack's gotchas, in the same shape, in
`harness/project/references/qa-gotchas.md` — read the matching sections of both. Add an entry
(with the failure that motivated it) whenever a pass misses something: here when it holds for
any stack (proposed upstream, never edited in place), in the project file otherwise; log it in
`harness/project/HARNESS_LOG.md`.

## Tests

- **A cache cannot prove an input it cannot see.** Name the external state, environment or
  generated/embedded input missing from the cache key; fix the key or bypass only that
  affected case/step. Keep ordinary build/result/dependency/image caches enabled. CI may use
  an explicitly declared hermetic result-cache policy; local gates do not disable caches
  suite-wide by default. Follow `harness/core/rules/efficient-testing.md`.
- **A package filter can miss a cross-package invariant.** Include affected boundary tests
  in other packages when their guards depend on the change. Start with that affected set
  locally; broaden for a concrete unresolved dependency/failure. The CI/pre-PR full suite
  remains required, rather than repeating the whole module for every local edit.
- **An ordering or invariant test must be proven by breaking the invariant.** Remove the guard
  (the state predicate, the scope predicate, the sticky update) and confirm the test goes red;
  a test that passes both ways pins nothing.
- **A red phase that never failed pins nothing.** The TDD red output must show the new
  assertions failing (or a compile error against the not-yet-written API) — not a skip, not an
  unrelated failure.
- **Fakes prove plumbing, not the contract.** A fake or in-memory dependency passing says
  nothing about the real dependency's error shapes; at least one test per boundary runs the
  real thing (the real database, a real object store).
- **A removal leaves tests that now prove nothing.** When a mechanism goes, every test written
  for it is deleted or rewritten; a survivor that still passes is dead weight, report it.
- **Parallel lanes share one container runtime.** Several worktrees running container-backed
  tests at once starve each other: cap the test parallelism, and treat a container startup
  timeout as a rerun, never as a result (pass or fail).
- **Test hashes are `git hash-object`.** `shasum`/`sha1sum` of the same file differs (git hashes
  `blob <size>\0` + content); a hash file in the wrong format reads as "tests edited". Record and
  compare with `git hash-object` only.

## Data access and generated code

- **Generated code drift.** Editing a code generator's source without regenerating still
  compiles against the old output. The gate runs the generator in its check or diff mode, which
  never writes files.
- **Paired definitions.** Two definitions that must stay identical (two queries feeding one row
  type, a schema and its copy) break far from the edit when one changes — or silently, if the
  types line up by accident.
- **Scope predicate.** Every user-path query carries the tenant or owner predicate; check each
  new query. A test that creates data for only one tenant cannot catch a missing predicate —
  scoping tests seed two.
- **Aggregates and NULL.** `avg` and similar aggregates over zero rows are NULL in SQL, while
  generated types may claim NOT NULL. Check the zero-row case returns what the API promises.

## Wire contract

- **Hand-written mirrors.** When server and client wire types are mirrored by hand, compare them
  field by field: name, optionality, `null` vs absent, enum string sets. Neither side's build
  sees the other; a renamed field compiles on both sides.
- **Dropping empty values on a null-meaningful field** turns "unknown" into "absent" and breaks
  consumers that branch on `null`.

## Auth and errors

- **A route without its auth declaration** may be public while the build stays green. Check
  every new route or operation for it.
- **Status codes.** Foreign objects answer like missing ones (404, not 403); duplicates map to
  409 from the database's unique violation, not from a check before the insert. The project's
  map lists the rest of its status contract.
- **Raw errors.** Any raw error string reaching a response body can leak internals (SQL, paths);
  everything goes through the project's error mapper.

## UI

- **A green build is not a working page.** A page that only fails at runtime (hydration
  mismatch, a client-only API called on the server) passes the build — open it in the browser.
- **States.** Every state the change can show (the spec's and those the code adds) is seen once
  in the running application before the change is called done; the design verifier's
  screenshots are that evidence.
- **A fresh worktree has no installed dependencies.** A lane that adds a dependency (or any new
  worktree) installs them before its tests mean anything; "module not found" there is the
  environment, not the code.
- **Worktree agents open URLs, not launch configs.** `preview_start` by name reads the main
  checkout's `.claude/launch.json` and serves the main tree; an agent in a worktree uses
  `preview_start {url}` against a server the orchestrator started.

## Config and deploy

- **Every place a variable lives.** A new config variable missing from the config reference or
  from any deploy description deploys as its default in production.
- **Internal endpoints stay internal.** After a reverse-proxy change, check that internal
  endpoints (metrics, debug) are unreachable from outside by the response body, not the status
  code: a redirect or a page from another service also answers 200 or 3xx.
