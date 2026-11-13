# Backend capabilities and remaining work

This release expands the high-priority translation paths and adds evidence/review backend services. It does not complete every SSIS runtime family. Unknown or unsupported source behavior remains represented and blocked. Actual private procedures, physical identities, audit-key meaning, target access, and runtime comparison evidence cannot be invented by the converter.

## Procedure programs

The bounded procedure translator now supports scalar local DECLARE initialization, SET assignment, optional SET NOCOUNT ON, and IF/ELSE with explicit BEGIN/END blocks. Local values are held in notebook-local state and evaluated through fixed native Spark SQL with named arguments. SQL NULL local values remain NULL, and IF UNKNOWN follows the false branch. Supported relational statements execute in source order. Only a final unconditional query can supply ordinal single-row task results.

At most 128 statements and 16 nested blocks are accepted. Unsupported commands, incomplete blocks, source session settings other than NOCOUNT ON, SELECT-variable assignment, cursors, WHILE, general EXEC, output/return parameters, and arbitrary TRY/CATCH remain pending. Transactions are not emulated: source conversion and separate-statement atomicity findings remain. The final result still uses the existing source scalar/nullability checks. No source Python/SQL program is dynamically evaluated.

## Scalar dates and clocks

GETDATE, GETUTCDATE, DATEADD, DATEPART, YEAR, MONTH, DAY, numeric subtraction, and checked numeric addition are supported in the scalar-expression subset. Calendar year/quarter/month addition clamps invalid destination days to the month's end. Supported date parts are year, quarter, month, day-of-year, day, hour, minute, second, and millisecond, with their standard aliases. Week/weekday and other session-dependent parts remain pending. Decimal/float mixing requires explicit casts.

Local GETDATE requires an explicit IANA source timezone through the existing `ssis_runtime_values` widget:

```json
{"$Runtime::SourceTimeZone":"Asia/Kolkata"}
```

GETUTCDATE uses UTC. Optional replay input `$Runtime::SourceClock` must be an ISO timestamp with UTC offset, for example `2026-11-13T07:00:00+00:00`; it represents one instant and is converted to the requested source/UTC timezone. The replay input is optional and does not become a manual requirement. Invalid timezone/clock inputs fail before writes. Local clock defaults, timestamp precision, timezone-rule differences, and source provider conversion still require runtime review.

## Additional data-flow paths

| Component | Implemented boundary | Still blocked |
| --- | --- | --- |
| Union All | Explicit input-to-output lineage on every input; matching captured/runtime types; duplicates and NULL values retained | Missing mappings, inferred NULL padding, implicit widening |
| Grouped Aggregate | Documented aggregation enums; explicit grouping/output lineage; COUNT/COUNT ALL/COUNT DISTINCT/SUM/AVG/MIN/MAX within checked type limits | Ungrouped empty-input behavior, string/NaN comparison policies, unsupported types; SUM requires Spark ANSI overflow checking |
| Merge Join | Explicit two-input key order and output lineage; inner/left/full join; captured TreatNullsAsEqual; duplicate multiplicity | Ambiguous source lineage, unsupported collation/NaN keys, missing type/key metadata |
| Lookup | Static mapped reference query, full cache semantics, unique compatible keys, explicit copied columns, fail-on-no-match | Partial/no cache, duplicate first-match selection, connected no-match routing, unreviewed string keys |
| Sort | Captured signed key priority, numeric/date keys, explicit NULL ordering; whole-row duplicate removal when every column is a sort key | Windows string/GUID collation, NaN ordering, survivor selection when non-key columns differ, output renames |
| Data Conversion | Same-type copies and explicit lossless numeric widening; checked captured WSTR length | Locale/code-page parsing, narrowing, ambiguous rounding, unsupported conversion types |
| Row Count | Distributed count into captured Int32/Int64 variable, preserving downstream frame | Source snapshot/nondeterminism and failure timing need runtime review; an extra source action is explicit |

The property enum/lineage interfaces follow Microsoft's [transformation property documentation](https://learn.microsoft.com/en-us/sql/integration-services/data-flow/transformations/transformation-custom-properties) and [Union All input-column schema](https://learn.microsoft.com/en-us/openspecs/sql_data_portability/ms-dtsx2/986dda96-5c22-4c03-b2b6-f823f2c2fd48). The native implementation is independently written. Runtime guards stop unsupported types and multiplicities rather than selecting arbitrary matches, truncating strings, or silently casting values. These are bounded translators, not guarantees for every package using a component with the same name.

Aggregate SUM records its required Spark ANSI setting in plan metadata. Generated notebook preflight checks this before earlier mutations such as TRUNCATE; it does not silently change the session setting. Missing source type/lineage policies remain generation findings, while unknown deployed schema types retain runtime guards.

Known built-in names and the documented MS-DTSX/MS-DTSX2 GUID identities are authoritative type evidence for supported component families. Misleading descriptions cannot override those identities. Unknown component IDs remain description-only hints and cannot certify native generation. GUID recognition does not imply that every older package encoding or numeric-ID graph layout is supported. The mappings use Microsoft's [class-ID enumeration](https://learn.microsoft.com/en-us/openspecs/sql_data_portability/ms-dtsx2/b2f96f16-3e16-414c-93ec-ceca000ebd34).

Lookup reference frames use DISK_ONLY persistence across guards and downstream consumers. Generated notebook cells release those resources in finally blocks; cleanup errors are retained separately and do not mask the original business exception. Stable source/reference snapshots and target cache-invalidation/concurrency behavior still require runtime validation. Row Count likewise needs stable source reads when its additional action precedes a downstream write. These checks do not establish transactional isolation.

## Backend services

`python -m ssis_migration.backend --help` exposes evidence and review operations. They do not deploy workloads or lift generation/runtime gates.

```sh
python -m ssis_migration.backend inspect-targets --plan output/migration_plan.json --output target_metadata.json
python -m ssis_migration.backend compare --source-table comparison.legacy.orders --target-table comparison.converted.orders --keys order_id --output comparison.json
python -m ssis_migration.backend review --package-result output/package_result.json --store reviews.sqlite --finding FINDING_ID --decision NEEDS_IMPLEMENTATION --author reviewer --note "Awaiting source procedure"
python -m ssis_migration.backend review-history --store reviews.sqlite --version MANIFEST_FINGERPRINT
python -m ssis_migration.backend revalidate --notebook reviewed.ipynb --package-result output/package_result.json --output revalidation.json
```

The inspection/comparison commands require an authorized Spark session with access to the named objects. No live source/target credentials are created by this CLI. Target inspection reads existence, provider, and column metadata only; it never resolves whether two legacy connections have the same business identity or creates/changes a table.

The comparison Python API is `ssis_migration.verification.compare_frames(source, target, keys=..., absolute_tolerances=...)`. Use stable authorized snapshots. Exact comparisons preserve duplicate multiplicities with distributed EXCEPT ALL. Keyed comparisons require non-null unique keys and report missing/extra keys and per-column mismatches. Numeric absolute tolerances are explicit, finite, decimal-bounded values. Schema type/nullability differences are reported. Complex types require explicit canonicalization. Data rows are not collected to the driver or embedded in the report. Counts and shuffles still require representative scale/performance validation.

Comparison evidence is scoped to DATASET; it never populates an overall legacy-match percentage or establishes control flow, transaction, audit, or complete workload equivalence. The main conversion report remains NOT_MEASURED until evidence is deliberately associated with the corresponding version and scope.

Review decisions are stored per manifest fingerprint/finding ID in SQLite with author, note, evidence references, UTC timestamps, revision checks, and append-only history through the API. An ERROR cannot be accepted as a WARNING. Recorded evidence is not a resolution or an execution bypass. Editing source/configuration generates a new version; previous decisions are not silently carried forward. Revalidation reads edited notebooks without overwriting them, checks native-code policy, retains supplied source blockers, and reports an artifact hash. A static revalidation pass is not runtime acceptance.

## Backlog by priority

| Priority | Area | Current status / remaining acceptance work |
| --- | --- | --- |
| Input blocker | Missing procedures and scripts, physical identity, audit-key meaning | Requires real source inputs/decisions; no generated substitute |
| High | Procedure/date/data-flow coverage | Bounded paths above implemented; extend remaining procedural statements, output/return parameters, source type/locale semantics, and row-error routing |
| High | Target auditing | Native adapter exists; real Delta updates/history, caller integration, concurrency/key semantics, failures, and statistics cost still need target validation |
| High | Behavioral verification | Dataset comparison backend implemented; actual legacy/target execution, workflow/failure/rerun baselines, and evidence association remain |
| High | Runtime semantics | Main-path conditional/failure/completion/OR execution, parallelism, transactions, checkpoints, and restart behavior remain blocked; require a separately validated execution contract |
| High | Dimensional business logic | Translate actual update/upsert/SCD bodies when supplied; no MERGE or history policy inferred from captions |
| Medium | Human review | Persistent backend and edited-code checks implemented; UI editing, cross-version proposals, and complete manual-implementation acceptance workflow remain |
| Medium | Project/orchestration coverage | Child packages, project/environment metadata, additional providers/task families, and cross-notebook state remain pending |
| Medium | Files and scripts | General File System/FTP/Execute Process and C#/VB/Script components require explicit translators; existing mail subset is retained |
| Medium | Metadata/configuration | Read-only target inspection implemented; richer column-policy configuration and source/project discovery still needed |
| Lower | Deployment/Volume intake | Authorized deployment, Volume browsing, environment profiles and production version visibility remain separate work |
| Ongoing | Operational proof | Broader runtime/version corpus, memory/scale tests, production permissions, and real source comparison are not established by local tests |

The remaining rows are not labelled complete because this release does not implement those contracts. The no-Spark/Pandas-UDF policy, confidentiality of supplied source files, and explicit pending-work accounting remain in force.
