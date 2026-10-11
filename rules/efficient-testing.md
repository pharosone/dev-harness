# Efficient testing

Test as cheaply as correctness allows. This core rule applies to every project, language,
runtime and test layer, including harness tests. Preserve the contract and realistic boundary
coverage; optimize the infrastructure and selection, never weaken assertions to save time.

## Choose the cheapest correct isolation

Apply these levels in order. Record the first level that preserves the behavior under test
and why cheaper levels cannot do so. Move up only for a concrete isolation requirement.

1. **In-process fake:** pure logic and deterministic boundary behavior need no service. Keep
   separate real-service contract tests for semantics the fake cannot establish.
2. **Shared long-lived service per machine:** isolate logical state with unique databases,
   schemas, transactions, buckets, prefixes, namespaces, vhosts, queues, topics, consumer groups
   or browser contexts. Reuse the process/container; give each project/run/worker/test an owned
   namespace. Cleanup removes only that namespace, including after a failed test.
3. **Per-run service:** use when the run changes process-wide configuration, credentials,
   startup behavior or other state that logical namespaces cannot isolate. Share it within the
   run and serialize conflicting tests where that retains the contract.
4. **Per-test container/process:** only when even per-run sharing cannot preserve the tested
   isolation, such as independently crashing/restarting a server or changing incompatible
   server-wide configuration concurrently. Write down the boundary and measured extra cost.

A shared test service is separate from production and developer-owned state. Namespaces must
include project and unique run/worker/test identity; a predictable test name alone collides
between concurrent lanes. Do not globally flush, truncate or delete another test's state.

## Service examples

| Boundary | Cheapest realistic setup | When to move up |
|---|---|---|
| Postgres | One test server; migrate a template once, keyed by migration contents, server version/extensions and fixture inputs. Close template connections, publish the completed template atomically, then clone unique test/run databases. A schema or rollback is cheaper when all tested writes stay inside it. | Rollback does not cover writes committed by another connection, and schema isolation does not cover database-global objects. Choose a database first, a separate server only for server-wide behavior. |
| MySQL | One test server; create unique logical databases and restore a cached migrated schema/fixture snapshot keyed by its inputs. Reuse transactions only for writes actually contained by the test transaction. | MySQL has no Postgres-style TEMPLATE clone. DDL can commit implicitly; rollback is not isolation for migration tests. Isolate the database or the server-wide behavior instead. |
| Redis | A shared test instance with owned key prefixes, separate users/permissions where needed, scoped cleanup and bounded expiry. | Redis Cluster supports database zero only. Prefixes do not isolate server-wide settings or eviction/flush behavior; use a per-run instance for those contracts. |
| S3 / MinIO | Reuse one test object service with an owned bucket or run/test prefix and scoped listing/cleanup. | A prefix groups keys; it does not isolate bucket policy, versioning, lifecycle rules or notifications. Use separate buckets for bucket-level tests, a service for server-level configuration. |
| Queues / Kafka | Reuse the broker; unique vhost/queue or topic plus consumer-group identity, deterministic drain/cleanup. Broker/client versions and fixture topology belong in setup keys. | Consumer groups isolate offsets, not the records in a shared topic. Broker-wide settings, failure/restart or cross-tenant permission contracts may need a per-run broker. |
| Browsers | Launch a browser once per worker/session and create a clean context per test; reuse compiled assets. Close contexts and release sessions in teardown. | Contexts isolate cookies/storage, not browser-process crashes or incompatible launch flags; those tests need a separate process. |
| External HTTP | In-process stub or recorded deterministic responses for ordinary unit cases; a bounded contract suite against a controlled shared test service for protocol/auth semantics. | A fake cannot prove the real provider contract. Isolate external mutable state with owned IDs; use scoped uncached probes only for freshness the cache cannot observe. |
| LLM calls | Deterministic fakes/fixtures for parsing, budgets and failure paths. Reuse immutable model downloads. Cache suitable results by prompt, model/version, parameters, tools and fixture/reference hashes. | A fake cannot establish model quality. Required paired evals use real measured calls, explicit ablations and reported model/cost/date; fresh provider behavior or a changed measured bundle requires a new measurement. |

The SQL, Redis, object and browser boundaries above follow the primary documentation:
[Postgres templates](https://www.postgresql.org/docs/current/manage-ag-templatedbs.html),
[MySQL implicit commits](https://dev.mysql.com/doc/refman/8.4/en/implicit-commit.html),
[Redis SELECT](https://redis.io/docs/latest/commands/select/),
[S3 prefixes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-prefixes.html),
[Kafka consumer groups](https://kafka.apache.org/28/operations/basic-kafka-operations/) and
[Playwright contexts](https://playwright.dev/docs/browser-contexts). Choose stronger isolation
for the specific boundary rather than assuming namespace separation covers every operation.

## Amortize setup and preserve caches

Run expensive setup once per machine/session/binary, not per test: migrations, immutable
fixtures, compiled assets, dependency/model downloads and container image builds. The key
includes content hashes of every meaningful input (migration order/content, seed data,
service/tool version, extensions, build flags and configuration). A cache hit must represent
that exact setup. Lock construction, publish only a successful immutable result, and create
isolated mutable state from it. Rebuild only when those inputs change.

Keep build, dependency, image-layer and correct test-result caches enabled. Disabling a cache
requires a written reason naming the input it cannot observe, then the narrowest affected
case/step. Never turn caching off across the whole suite as a generic freshness fix.
For example, the Go result cache cannot establish a fresh external database's current state:
use `go test ./internal/store -run '^TestLiveDBContract$' -count=1` for that probe, with the
reason recorded; keep compilation/dependency caches and ordinary tests cached. Blanket
`go test ./... -count=1` is not a default local validation command. See
[Go test cache](https://pkg.go.dev/cmd/go#hdr-Test_packages).

Cached evidence is accepted only for matching inputs. Changed skill instructions/references
require fresh paired measurements under the skill-eval contract; a mocked result is never
measured model evidence. CI may deliberately use stricter hermetic settings and disable a
result cache in its declared gate without disabling build/dependency caches everywhere.

## Select work and share the machine budget

Locally run the narrowest affected tests first: a changed file/test, its package/module and
relevant boundaries. Expand when failures, dependencies or unresolved evidence justify it.
The full suite belongs in CI and the pre-PR gate; do not rerun it in every unit or lane after
sufficient targeted evidence. Record the reason for each broader run. The required full suite
is still required once at that gate, even if all focused tests passed.

Bound runner/package/browser parallelism by actual available CPU/memory and observed test
cost; no unbounded child fan-out. Reuse setup before adding workers. Heavy full gates,
paired evals and cross-review use the adaptive host load guard: idle capacity starts at once;
only observed busy load/memory makes a job wait, for a bounded time. This is not a fixed slot
count. Nested work in the admitted process tree does not wait again; targeted runs never wait.
CI stays hermetic and can explicitly configure/disable advisory local load guarding.

## Evidence in the unit brief and QA

A unit adding test infrastructure must state its isolation level, correctness boundary,
namespace ownership/cleanup, reusable setup hash, cache exception (or none), affected local
commands and parallelism budget. Use `harness/core/prompts/implementer-brief.md`.

Report measured cold setup and warm test wall time, container/process starts, CPU/memory
observations (or explicitly unknown), and the shared fixture/service scope. Compare equivalent
work and separate guard wait from execution. QA checks the choice against this rule, confirms
real boundary coverage remains and challenges unexplained per-test infrastructure or blanket
cache bypass. Gate timing regressions over 50% of the prior rolling median are advisory:
inspect them, report a cause or next measurement, and preserve the step's real pass/fail.
