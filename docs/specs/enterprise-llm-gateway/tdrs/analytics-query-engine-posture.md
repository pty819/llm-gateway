# TDR: Analytics Query Engine Posture

## Status

Proposed. Current recommendation: keep PostgreSQL as the source of truth and the
first analytics engine, but split analytics SQL from ORM-owned business logic and
prepare a DuckDB mirror path for heavier OLAP.

## Decision

Capacity analytics should not be treated as ordinary CRUD. The gateway should use
SQLModel/ORM patterns for policy, auth, configuration, and durable fact writes,
while analytics reads should live behind an explicit query repository that can run
hand-shaped SQL against PostgreSQL now and another analytical backend later.

## Context

The product now records request facts with timing, token, retry, fallback, subject,
project, model, streaming, and vLLM-adjacent performance fields. Admin APIs expose
summary, ranking, time-bucket, and drilldown queries.

The user wants detailed operator analysis by time window, person, project, model,
latency, TTFT, stream duration, retry/fallback overhead, and future vLLM serving
signals. The user also prefers a lean stack: PostgreSQL plus Redis first, avoiding
ClickHouse-class operational weight until evidence justifies it.

## Current SQL Performance Risks

| Query shape | Current risk | Why it matters |
| --- | --- | --- |
| Usage summary grouped by `model_alias`, `subject_id`, `project_id` | Full retained-table aggregate when no time range is provided | Every extra retained raw fact adds CPU and I/O even when the UI only needs recent pressure |
| Ranking by subject and token totals | Aggregates before top-N ordering | A normal index cannot directly satisfy `ORDER BY sum(tokens)` across arbitrary windows |
| Time buckets with `date_trunc(started_at)` | Range filtering can use `started_at`, but grouping still scans and hashes/sorts matching rows | Minute buckets over long windows grow quickly and are easy to over-request from the UI |
| Drilldown over multiple dimensions | High-cardinality group-by plus optional joins | Subject/project labels require joins after scanning and grouping candidate facts |
| Optional filters on model, subject, project, outcome, streaming | Single-column indexes are not enough for common compound predicates | PostgreSQL may bitmap-combine indexes or choose sequential scans as volume grows |
| JSONB detail metrics | Cheap to store, expensive to aggregate ad hoc | Repeated JSON extraction in dashboards would add CPU and make indexes harder |
| Analytics reads on the OLTP primary | Dashboard scans compete with request-path inserts and admin CRUD | The gateway should stay responsive even when someone opens a large report |

The current code is not doing the worst possible ORM pattern: it builds aggregate
SQL with SQLAlchemy expressions and returns mappings. The coupling problem is more
subtle: analytics SQL lives in API route code and references the `RequestFact` ORM
model directly, so switching to rollups, materialized views, raw SQL, DuckDB, or a
read replica would force endpoint-level changes.

## PostgreSQL First Optimizations

PostgreSQL can carry the MVP and the first production load if the dashboard is
disciplined:

1. Require or default meaningful time ranges for every expensive analytics view.
   The UI should default to recent windows such as 24 hours or 7 days, not all
   retained data.
2. Cap bucket count. For example, minute buckets should be limited to short
   windows; long windows should automatically move to hour or day buckets.
3. Add analytics-specific indexes outside SQLModel field declarations:
   - BRIN on `request_facts(started_at)` for large append-correlated scans.
   - B-tree composites for common filters: `(started_at, model_alias)`,
     `(started_at, subject_id)`, `(started_at, project_id)`, and possibly
     `(model_alias, started_at)` when model-specific dashboards dominate.
   - Partial indexes only after measured evidence, for example failed outcomes or
     streaming requests if those views become hot.
4. Partition `request_facts` by `started_at` once raw retention reaches the point
   where old data hurts query planning, vacuum, backup, or deletion. Monthly
   partitions are a conservative start; daily partitions are justified only if
   volume is high enough.
5. Introduce rollup tables before adding a new database:
   - `request_rollup_minute` keyed by bucket start plus model, subject, project,
     endpoint family, outcome, and streaming.
   - Hour/day rollups derived from minute/hour data.
   - Counts, token sums, retry/fallback sums, latency aggregates, TTFT/stream
     aggregates, and cache/prefill/decode metrics where available.
6. Keep raw facts for audit-grade drill-through, but answer dashboard defaults from
   rollups.
7. Run `EXPLAIN (ANALYZE, BUFFERS)` on representative 1M, 10M, and 50M row
   datasets before declaring PostgreSQL insufficient.

## DuckDB Postgres Extension Feasibility

DuckDB is a strong candidate for embedded OLAP, but only in the right shape.

### What Works Well

- DuckDB can attach PostgreSQL and query Postgres tables through its Postgres
  extension.
- DuckDB can load data from PostgreSQL into DuckDB tables or copy it to Parquet.
- The SQL surface is natural for analytical reports and does not need the ORM.
- Embedded deployment keeps the stack lighter than ClickHouse, Druid, or a hosted
  warehouse.

### What Does Not Solve The Core Bottleneck

Using DuckDB to directly query PostgreSQL on every dashboard request does not make
the source data free. The dashboard still reads the raw table from PostgreSQL at
query time. DuckDB may aggregate faster than PostgreSQL for some OLAP shapes, but
PostgreSQL and the network still pay for scanning and transferring the rows. For a
large append-only fact table, that can simply move the compute boundary while
leaving the OLTP primary under pressure.

### Recommended DuckDB Shape

Use DuckDB as a mirror, not as a live scanner for the hot dashboard path:

1. PostgreSQL remains the source of truth for facts and config.
2. A periodic analytics job copies deltas from `request_facts` into a local DuckDB
   file or Parquet dataset.
3. The dashboard reads DuckDB/Parquet for heavy ad hoc scans and long-range
   historical reports.
4. The Postgres extension is useful for bootstrap, backfill, and delta copy, not as
   the steady-state query path for every operator screen.
5. The writer job is single-owner. API workers open read-only DuckDB connections
   or query through one analytics service process to avoid file-lock and
   multi-process write surprises.

## Alternatives

| Option | Fit | Risk |
| --- | --- | --- |
| PostgreSQL raw facts only | Simplest MVP | Dashboard latency grows linearly with retained facts |
| PostgreSQL plus rollups and partitioning | Best near-term fit | Requires disciplined query and migration work |
| PostgreSQL read replica for analytics | Good if primary load is the main issue | Still row-store OLAP; adds database operations |
| DuckDB direct Postgres scans | Good for exploration and backfills | Still stresses PostgreSQL for large repeated scans |
| DuckDB mirror or Parquet lake | Best lightweight OLAP escalation | Needs refresh jobs, consistency handling, and file/concurrency discipline |
| TimescaleDB | Useful continuous aggregate path inside PostgreSQL ecosystem | Adds extension dependency and operational behavior to learn |
| ClickHouse | Strong long-term OLAP ceiling | Too heavy for current stated preference without evidence |

## Recommended Architecture Boundary

Add an analytics repository boundary before optimizing engines:

```text
FastAPI route
  -> AnalyticsService
      -> AnalyticsRepository interface
          -> PostgresAnalyticsRepository now
          -> DuckDBAnalyticsRepository later
```

Rules:

- Business/config/auth logic can keep using SQLModel and ORM-style helpers.
- Analytics reads should return DTOs, mappings, or Pydantic response models, not ORM
  entities.
- Complex dashboard queries should live in an analytics module or SQL files, not
  inside `api/admin.py`.
- The API contract should not expose whether a result came from raw facts, rollups,
  materialized views, a replica, DuckDB, or Parquet.
- Fact writes must stay simple and reliable; heavy aggregation should happen
  asynchronously or on read replicas/mirrors.

## Proposed Phases

### Phase 1: Make PostgreSQL Boring And Bounded

- Default every admin analytics endpoint to a time window.
- Add bucket-count protection.
- Move analytics SQL out of API routes.
- Add BRIN and the first composite indexes.
- Add query-plan regression checks for representative analytics SQL.

### Phase 2: Rollups Before New Infrastructure

- Add minute/hour/day rollup tables.
- Backfill rollups from raw facts.
- Refresh rollups incrementally with an async worker or scheduled job.
- Let dashboards prefer rollups and fall back to raw facts only for narrow
  drill-through.

### Phase 3: DuckDB Mirror For Heavy OLAP

- Add a DuckDB-backed repository implementation behind the same service contract.
- Copy append-only deltas from PostgreSQL to DuckDB or Parquet.
- Restrict DuckDB writes to one process/job.
- Use read-only dashboard connections or a single analytics service.
- Keep PostgreSQL rollups as the online fallback.

### Phase 4: External OLAP Only With Evidence

Move to ClickHouse-class infrastructure only if embedded OLAP or PostgreSQL rollups
fail measured requirements for retention, concurrency, or query latency.

## Suggested Escalation Thresholds

These are planning thresholds, not hard guarantees:

| Retained facts | Recommended posture |
| --- | --- |
| Under 1-5M rows | PostgreSQL raw facts with bounded windows and basic indexes is likely enough |
| 5-50M rows | PostgreSQL needs BRIN/composite indexes, partitioning, and rollups |
| 50M+ rows or frequent long-range ad hoc drilldown | Add DuckDB mirror or another OLAP path |
| High dashboard concurrency with long history scans | Use rollups/read replica/OLAP; do not point every scan at the OLTP primary |

For 400 users, rough daily volume can vary widely:

- 100 requests/user/day -> 40K facts/day -> about 1.2M/month.
- 500 requests/user/day -> 200K facts/day -> about 6M/month.
- 2,000 requests/user/day -> 800K facts/day -> about 24M/month.

This is well within PostgreSQL storage territory, but not automatically within
"scan months of raw facts for every dashboard" territory.

## Migration And Verification Requirements

- Add migrations for indexes, partitions, and rollups explicitly; do not rely on
  SQLModel auto-create for analytics structures.
- Measure with `EXPLAIN (ANALYZE, BUFFERS)` before and after each index/rollup
  change.
- Keep a synthetic fact generator for 1M/10M/50M row local benchmarks.
- Track dashboard query p95 latency, rows scanned, shared buffer reads, temp file
  usage, and primary database CPU during reports.
- Treat DuckDB mirror freshness as part of the UI contract, for example "updated
  through 2026-05-27 09:30".

## Decision Summary

The user's instinct is correct that analytics should not be tightly coupled to the
high-level ORM. The stronger conclusion is: do not replace PostgreSQL immediately;
replace the analytics boundary first. PostgreSQL plus Redis remains the right
source-of-truth posture for the MVP, but advanced analytics should be designed as
an engine-swappable SQL layer. DuckDB is the preferred lightweight OLAP escalation
when it is used as a copied/mirrored analytical store, not as a repeated live scan
of the OLTP primary.

## External References

- DuckDB PostgreSQL extension: https://duckdb.org/docs/current/core_extensions/postgres.html
- DuckDB concurrency model: https://duckdb.org/docs/current/connect/concurrency.html
- PostgreSQL BRIN indexes: https://www.postgresql.org/docs/current/brin.html
- PostgreSQL table partitioning: https://www.postgresql.org/docs/current/ddl-partitioning.html
- PostgreSQL materialized views: https://www.postgresql.org/docs/current/rules-materializedviews.html
