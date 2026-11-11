# SSIS Conversion & Assessment Accelerator

Generate Databricks Spark SQL/Python artifacts and report what still needs implementation or verification. Generated artifacts do **not** certify legacy equivalence. Runtime behavioral match is reported as `NOT_MEASURED`, with no invented accuracy percentage.

## Install and run

```sh
python -m pip install -e '.[dev]'
streamlit run frontend/app.py
```

For AI-assisted **legacy-mode** generation, install `.[ai]` and configure the selected provider. AI is disabled by default. The source-graph planner uses deterministic native translation and explicit blockers for unsupported work. No Databricks workloads are executed or deployed by this tool.

```sh
python -m ssis_migration.migrate \
  --source examples/wwi/DailyETLMain.dtsx \
  --output ./assessment-output \
  --base-path . \
  --catalog dev_catalog \
  --layer silver \
  --procedure /path/to/procedures-a.sql \
  --procedure /path/to/procedures-b.txt \
  --ddl /path/to/tables.sql
```

Use `--dry-run` to parse, resolve, and write the plan/diagnostics without final notebook generation or AI calls. Graph mode writes review drafts even with `--strict-mode`: named pending sections retain incomplete work, and unresolved errors block execution before data operations. Strict CLI still exits nonzero when review is required. Legacy strict mode continues to withhold incomplete notebooks.

CLI exit status is nonzero for technical failures and, in strict mode, packages requiring review. The Python API always returns a structured report for package-level failures. Invalid top-level configuration raises an exception. `--execute` is unsupported.

## Inputs

- One or more mandatory `.dtsx` files through the frontend, or a file/directory of packages through the CLI/API. Each selected frontend batch is isolated from earlier uploads; duplicate filenames receive separate output identities and malformed packages do not stop valid packages.
- Multiple optional procedure files (`--procedure`, repeatable), and DDL files (`--ddl`, repeatable).
- Optional target notebook/library references (`--reference`, repeatable): ZIP, Databricks Python/HTML exports, IPython notebooks, or SQL/text supplied explicitly as reference. References are inspected without execution and do not supply legacy procedure/DDL definitions.
- Optional connection mapping JSON with a `mappings` list. Referenced metadata files are resolved against `--base-path`.
- Logical layer: Bronze, Silver, Gold, Multi-layer, unspecified, or custom text. No LLM is used to select it.
- Optional `--object-mappings objects.json` overrides default naming with fully qualified Databricks objects. Scoped keys use `connection_reference|schema.object`; unscoped keys deliberately apply across connections.
- Optional `--dependency-scopes scopes.json` assigns procedure/DDL file names to connection references. Ambiguous definitions are retained as findings rather than broadcast to every database.
- Optional `--target-schema` and business description. With a schema, graph mode proposes `catalog.schema.lowercase_table` names and records them for review. Without it, unresolved targets remain placeholders. Different legacy identities proposing one target produce a collision finding. Multi-layer output overrides include their own `layer`.
- The default `--planner-mode graph` follows source task/port identities. `--planner-mode legacy` retains the previous warehouse-specific rules for comparison.

The existing `procedures_path=` and `ddl_path=` API arguments are retained; new callers can use `procedures_paths=[...]` and `ddl_paths=[...]`.

All supporting files are consumed. A mixed procedure/DDL file can contribute to both collections. Conflicting definitions are rejected instead of silently choosing a source. Identical repeated definitions are accepted. Unqualified calls use the configurable `default_schema` hint (default `dbo`); this is not a claim to reproduce every SQL Server principal-specific resolution rule.

## Outputs and interpretation

Start with the Markdown review and `_Migration.ipynb` draft. The UI keeps diagnostic files behind an advanced option. JSON artifacts are records, not scripts to execute.

`migration_report.json` is the run index. Each package/input version contains:

- `migration_manifest.json`: content hashes, engine/configuration identity, and artifact paths.
- `package_ir.json`, `analysis.json`, `migration_plan.json`, `source_inventory.json`.
- `task_graph.json`, `dataflow_graphs.json`, and `dependency_inventory.json` preserve identities, scopes, graph edges, and definition provenance.
- `verification_plan.json` lists required comparisons. It contains no executed comparison results.
- `operations.json`: original generation output for diagnosis, including rejected content.
- `validation_results.json`, `assessment.json`, and a Markdown review report.
- `pending_items.json` indexes named sections with stable IDs, original SQL, connections, procedure availability, and bindings.
- With reference inputs, `reference_inventory.json` and `reference_review.md` classify each archive/member and report bounded table/library contract findings. Same-named archives remain distinct through their content hashes.
- A `_Migration.ipynb` graph review draft for every non-dry-run assessment. The filename suffix is retained for compatibility; it does not indicate certified migration.

Incomplete graph notebooks have `notebook_status = DRAFT_REQUIRES_REVIEW` and `execution_readiness.status = BLOCKED_BY_REVIEW`. Static checks passing still do not imply runtime equivalence.

Statuses include `REVIEW_REQUIRED`, `ARTIFACTS_GENERATED`, `ANALYZED`, `PARTIAL_FAILURE`, and `FAILED`. There is no unconditional `MIGRATED` result.

Operation coverage is the percentage of generated business operations passing native-code static checks. Container bookkeeping and intentionally replaced audit operations are validated and counted separately. They do not inflate coverage. It is **not** task coverage, runtime validation, or behavioral accuracy. All source tasks/components have a disposition; unsupported standalone work is retained as a review step rather than disappearing.

Content-addressed output versions change with source/dependencies, referenced metadata, configuration, and engine code. Same-input findings retain stable IDs. Generated notebook hashes are recorded in the manifest. A changed notebook is preserved even without a review marker; explicitly reviewed, unreadable, and untracked notebooks are also protected. Preserved outputs stay downloadable and require revalidation. They do not count as newly analyzed or generated packages. These policies concern generated artifacts, not data-pipeline rerun safety.

## Current boundary

The source-graph planner supports standalone relational SQL, explicit table overrides or visible default naming, sequence boundaries, success-AND precedence, static mapped OLE DB sources, Multicast, a restricted Derived Column expression subset, and ordered Conditional Split outputs. It uses exact port IDs and never flattens a multi-source graph into the first source.

The default destination policy preserves captured source behavior. Authoritatively identified OLE DB table/FastLoad destinations with static targets generate native inserts into existing mapped tables. A separate source TRUNCATE remains a separate preceding task; the destination does not add another truncate or overwrite. Exact lineage, external column mappings, types, and target columns are checked. Unknown modes remain pending. Use `--destination-write-mode review` for projection-only drafts; `append_existing` explicitly requests the same guarded insert path. Neither mode creates tables or selects MERGE keys.

Source access modes select the active table, SQL command, or variable. Inactive captured SQL is not used as dependency evidence. Variable-backed sections retain lexical scope and declarations. Supported SQL expressions made of literal strings plus numeric value references become native named SQL arguments; source expressions are never run through Python eval or runtime SQL string concatenation. Other expressions remain pending.

Missing procedures produce named draft sections. Supplying a definition regenerates the source step in a new input version. Supported literal/default/named/positional input arguments and task-variable bindings are matched to formal parameters, cast, and inlined into bounded relational Spark SQL bodies. General procedural bodies, output/return parameters, and ambiguous conversions remain pending with their definitions recorded.

A flat, ordered package OnError handler can run after a main step fails, preserving supported success/failure/completion and expression constraints inside that handler. Main-path conditional constraints and nested/parallel handlers remain blocked. Native Send Mail Tasks can retain captured unauthenticated SMTP/STARTTLS settings. A bounded C# ScriptMain mail pattern can also become native Python after its complete entry-point structure and supported statements are parsed. A task label does not establish behavior. Other captured C#/VB programs remain review work; missing programs remain source gaps.

Loops, general Script Tasks/components, joins/lookups/aggregates, general procedural SQL, unsupported bindings, row-error redirection, checkpoints, forced results, and cycles remain explicit blockers. Independent task branches require a concurrency/failure-isolation decision before executable notebook acceptance. The optional legacy planner still uses warehouse/name/control-table conventions and reports those assumptions.

No deployment, runtime equivalence measurement, Volume browsing, or new pipeline/load strategy is implied. See [the source-graph guide](docs/source-graph.md) for mapping examples, supported subsets, and validation steps.

Static validation rejects visible Spark UDF/Pandas UDF/UDTF definitions and registration, selected custom row APIs, dynamic Python execution, unresolved SQL parameters, legacy EXEC passthrough, incomplete bodies, and unbounded driver collections. Ordinary Python helpers and bounded retrieval of up to two rows for single-row cardinality checks remain supported. This is a conversion-quality check, not a sandbox for arbitrary Python.

See [the assessment contract](docs/assessment-contract.md) for details and follow-on acceptance criteria.

## Native runtime bindings

Named input parameters, ordinal single-row results, and bounded relational batches can generate native Spark SQL code. The notebook `ssis_runtime_values` JSON widget supplies external values, using the keys listed in the generated Native runtime bindings section. Captured literal variable defaults are retained; supported result bindings update shared notebook state. Missing inputs, unsupported NULL conversions, integer overflow, and incompatible scalar types fail explicitly.

Supported computed variables are evaluated from their source expressions at each read, using ordinary Python helpers. They do not become extra manual inputs or Spark/Pandas UDFs. The bounded subset includes typed additions, selected scalar casts, and UTF-16 SUBSTRING/LEFT/RIGHT. Captured non-sensitive, non-required package-parameter defaults are retained; required or sensitive values must be supplied externally. Unknown expressions remain pending.

Expression Tasks capture their source Expression and support one assignment to a declared User variable using the same scalar subset. Unsupported functions, including GETDATE/DATEADD/DATEPART, retain the original expression in a named pending section. No cutoff clock or timezone policy is guessed.

Runtime binding records distinguish external inputs, captured defaults, derived expressions, source result producers, failure context, and audit value bridges. `required_external_input` identifies missing external values. Source mode retains source auditing; target mode records its replacement separately. Captured zero audit-count defaults are never presented as measured counts.

Generated graph notebooks show execution readiness, blocking codes, and a typed input template at the top. Nulls in that template must be replaced; they are not runnable defaults. Initial values and eligible source-derived conversions are checked before audit setup or business mutations, including a source TRUNCATE. Source assignment/result values produced along prior task dependencies are distinguished from initial inputs; read-before-assignment and read/write variables still require an initial value. Later data-derived values retain checks at their source read points.

Shared-state notebooks group replaced audit tasks and structural/disabled sections into review tables, keeping executable cells focused on business work. The complete source dispositions and dependencies remain in the diagnostic task graph; grouping does not alter business ordering or the runtime review gate. Missing procedure bodies remain named pending sections rather than commented-out success paths.

## Existing framework references

Use the frontend reference role or `--reference path/to/framework.zip` for target configuration, database-definition, and library notebooks. This role stays separate from SQL Server procedure/DDL inputs. Two similarly named cfg archives are classified per member by their contents. A database-definition notebook can contain DDL; it is still target reference evidence, not a source schema override.

The inspector reads supported notebook cells, including encoded Databricks HTML models, without importing Python, running `%run`, executing setup SQL, sending notifications, or installing UDFs. It records function/class signatures, DDL columns, supported subsequent ADD COLUMN changes, explicit INSERT column contracts, and include candidates. Raw notebook bodies, outputs, credential values, and configuration row data are not copied into its inventory or generated workload. Unsupported formats/SQL/Python remain reference review findings.

Contract comparisons use unique column signatures by table name and retain the need to verify dynamic catalog/schema identities. Conflicting definitions and missing INSERT columns are findings, not automatic table repairs. Native-UDF and unbounded-driver-collection policy findings identify reference code that cannot be assumed compatible with generated workloads. Reference code is never automatically imported. Target auditing uses the native adapter described below.

ZIP intake validates paths, duplicate entries, symlinks, nested/encrypted archives, member counts, and compressed/expanded size bounds. Referenced content hashes participate in output versions and reviewed-output protection. Only metadata is emitted; support archives remain input files. No connection to a live Databricks environment or verification of deployed table versions is implied.

## Target audit framework

The frontend defaults to **Target audit framework**. Set the existing audit schema and optionally a different audit catalog. The schema must already contain compatible `audit_notebooks` and `audit_tables` Delta tables; the converter never creates or alters them. API/CLI callers retain source mode by default for backward compatibility. Select target mode explicitly:

```sh
python -m ssis_migration.migrate --source package.dtsx --output ./review \
  --catalog data_catalog --target-schema data_schema \
  --audit-mode target --audit-catalog existing_catalog --audit-schema existing_audit_schema
```

The adapter writes the existing framework's notebook/table audit storage contract through literal, bound Spark SQL. It does not import the shared workflow library or register its UDF. Required column names/types are checked before business work. Optional job columns are used only when present with compatible types. Unknown required columns, missing schemas/tables, and incompatible versions stop execution instead of triggering automatic DDL.

Source audit replacement uses explicit INSERT column signatures, connection/object identities, variable producers/consumers, and a recognized audit-notification chain. Task labels are not classification evidence. Audit-only row counts can be replaced; counts still consumed by business work are preserved. Mixed business/audit SQL, unknown recovery tasks, unsupported handlers, and embedded source-audit writes remain review work. `audit_replacement_plan.json` retains every replaced source reference, original SQL/bindings, reason, and business value bridge. Replaced audit operations remain in source accounting but are excluded from operation coverage through `audit_replaced_operation_count`.

At runtime, a standalone run records Running then Success/Error. Passing `execution_key` attaches to one existing Running framework record without a duplicate INSERT; completed/ambiguous keys are rejected. Attached success returns `[$OK][$STR]` for the framework caller. Job metadata is read from the target Databricks context when available. `target_audit_job` can explicitly supply `job_id`, `job_name`, `job_run_id`, `master_key`, `execution_key`, and `source_file_path`. Root keys use positive UUID-derived Int64 values; target audit identity/lineage and concurrency still need runtime review.

Table statistics cover known mapped write targets, using distributed counts and at most two metadata rows per lookup. Before/after metrics are recorded on success and attempted on failure; missing metrics are not invented. Statistics errors and audit-write errors never replace the original business exception. A failed/finished execution cannot continue writing business cells or report a later false success. Business mutations and audit writes are separate target transactions; no rollback or data rerun guarantee is implied. Use `--no-audit-table-stats` when those Delta statistics are not part of the intended target contract.

An audit value consumed by business SQL cannot simply disappear. Choose a binding with `--audit-value-bindings bridge.json`, for example `{"User::BusinessAuditKey":"runtime_input"}`. Supply that explicitly chosen business value through `ssis_runtime_values`. `notebook_key` is permitted only for a captured Int64 variable; Int32 values are never silently narrowed from the target execution key. Stale source defaults are removed. Missing bindings, incompatible types, and unavailable business procedure bodies remain blockers. Target auditing replaces source audit/error notifications; it does not fabricate the missing business transformation or automatically send source emails.

Generated target-audit notebooks require `step_id=all`. Use one notebook per workload; per-step jobs cannot share audit state. Source mode remains available for controlled comparison.

Different source frameworks can add column signatures, connection-scoped objects, or package-scoped audit leaf-task rules with `--source-audit-rules`. Existing target tables/columns and lifecycle status values can be mapped with `--audit-contract`. Both JSON settings are also available in the frontend and Python API. See [audit configuration and support boundaries](docs/audit-configuration.md) for examples, required role types, batch behavior, and unsupported workload handling. The extensions generalize classification/storage; they do not certify every SSIS task or arbitrary audit semantics.

The package OnError handler records source name/description and target exception text. Its `ssis_error_codes` widget requires an explicit target-error-class to source audit-code mapping when the source SQL reads ErrorCode; no legacy numeric code is invented. Handler failures are recorded and the original main failure is re-raised. SSIS propagation and fast-load/transaction differences remain review findings.

This subset requires Spark 3.4+ native named SQL arguments and one shared notebook run (`step_id=all`). Per-step Jobs are blocked until a state-transfer design is supplied. Input/provider conversions and timestamp policy remain review items. INSERT followed by MAX receives an identity/concurrency blocker; separate calls receive an atomicity review warning. The tool does not invent identity columns or a load strategy.

Recognized `EXEC('SELECT COUNT(*) FROM ' + @TableName)` uses a captured non-expression table declaration mapped at generation time. A runtime change to that table name requires regeneration. Arbitrary dynamic SQL remains pending.

## Tests

```sh
pytest -q --ignore=tests/agent/test_live_llm_integration.py
```

The live integration module requires a separately configured Databricks endpoint; do not use it for offline validation. Source code and examples in this repository are the only inputs used by automated tests. Confidential third-party tool archives are not included.

Optional real-Spark checks use synthetic local tables only:

```sh
python -m pip install pyspark
SPARK_LOCAL_IP=127.0.0.1 pytest tests/test_bound_sql_spark.py -q
```

These checks validate native arguments/results, derived dates, ordered SQL, and mapped inserts with schema/length guards. They do not measure SSIS equivalence or Databricks Delta behavior.
