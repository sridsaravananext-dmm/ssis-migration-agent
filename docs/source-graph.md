# Source-graph backend and physical mappings

The default engine is now `graph`. It plans from source references, hierarchy, precedence, and data-flow ports rather than from task names. The previous engine remains available with `--planner-mode legacy` for comparison. No third-party implementation or source archive is included.

## Try the backend without executing a workload

```sh
python -m ssis_migration.migrate \
  --source examples/graph/relational.dtsx \
  --output ./graph-assessment \
  --catalog dev_catalog \
  --layer silver \
  --object-mappings examples/graph/objects.json \
  --strict-mode
```

This is a minimal parser/generator fixture, not a deployable SSIS solution or an executed Databricks demonstration. Its SQL already specifies an INSERT and a subsequent SELECT, so translation preserves that supplied statement behavior. Running the accelerator only generates/assesses artifacts. Never infer safe data reruns from this example's plain INSERT.

Expected: both standalone tasks are represented, their success dependency is retained, generated SQL references the configured objects, and no WWI cutoff/lineage helpers appear. Static coverage can be 100% for this fixture while behavioral match stays NOT_MEASURED. Add `--dry-run` to write only plan/assessment artifacts without a final notebook or AI calls.

## Draft-first generation

A `.dtsx` alone is sufficient for a draft. Missing procedure definitions and unknown targets remain open review items with named pending sections; they do not prevent notebook writing in graph mode. Dry run still omits the notebook. Strict mode still returns a nonzero CLI status for review-required runs, but graph drafts remain available. Legacy strict behavior is unchanged.

Start with the notebook draft and Markdown review. The UI provides these downloads first and hides diagnostic artifacts by default. Do not run the JSON files. `execution_readiness.status` distinguishes BLOCKED_BY_REVIEW from STATIC_CHECKS_PASSED; both retain NOT_MEASURED runtime verification.

`pending_items.json` and notebook cell metadata connect stable pending IDs to source steps. Sections retain original SQL (commented only), logical connection, procedure names and definition availability, parameter/result bindings, and data-flow source variables or destination names where applicable. Both a package preflight and pending-section runtime errors prevent unfinished work from silently succeeding.

When procedure files arrive, regenerate. Dependency hashes create a new output version and reviewed outputs remain protected. A pure argument-free EXEC of a parameterless procedure with one supported relational statement can replace its pending section with native Spark SQL. Optional BEGIN/END wrapping is supported. Multi-statement bodies, declarations, execution options, arguments, return values, and bindings remain pending; definition availability alone is not translation success. No UDF is generated.

## Target naming and optional overrides

Set `--catalog dev_catalog --target-schema develop_dev_silver` to propose `dev_catalog.develop_dev_silver.lowercase_table` for legacy objects. This is deterministic naming, not environment discovery. The proposal, original object, connection, role, and resolution (`CONVENTION`, `EXPLICIT`, or `PLACEHOLDER`) are recorded in mapping evidence. Review proposals and confirm the intended target tables. Logical layers alone do not select a physical schema.

Leave target schema blank to retain a visible `__pending_target_schema__` location and pending source/SQL sections. An unresolved location is never counted as implemented. Configuration fields accept a single catalog/schema identifier, not descriptions such as `silver - schema name`.

Different legacy schemas/databases/connections may contain the same table name. If a convention would collapse distinct identities to one target, TARGET_NAME_COLLISION blocks execution. Optional per-object overrides resolve renames and collisions. Same-named references in different connections require an explicit shared-identity decision or overrides; the tool does not infer that ADO.NET and OLE DB connections access the same physical database.

Override example:

```json
{
  "source_connection|dbo.Customer": {"target": "catalog.bronze.customer", "layer": "bronze"},
  "target_connection|dbo.Customer": {"target": "catalog.silver.customer", "layer": "silver"}
}
```

Keys identify legacy objects. The optional prefix is the exact logical connection reference recorded in package IR/dependency inventory. A connection-scoped key takes precedence over an unscoped key. An unscoped key intentionally maps that object across all connections; it is not a fallback to a guessed schema. Unqualified legacy objects use `default_schema`.

Explicit per-table mappings are optional; they override the visible naming convention. CTE aliases remain local query names. Targets must be three-part Databricks identifiers; expressions, extra SQL, and target aliases are rejected. Source aliases are preserved, including a renamed physical source table when its original qualifier is used. Database-qualified column references, embedded-dot source identifiers, table-valued/custom functions, temporal/hinted access, unsupported statements, and unbound parameters remain review requirements.

For a single-layer package, the configured target schema must agree with explicit write overrides. Multi-layer packages require an explicit layer on each output mapping. Read/source mappings can belong to a different layer from the package's output layer. Notebook catalog/schema widgets cannot silently override a mapping version; changing them requires regeneration.

## Dependency scopes

```json
{
  "source_procs.sql": "source_connection",
  "target_procs.sql": "target_connection",
  "source_tables.sql": "source_connection"
}
```

Supply this object through `--dependency-scopes scopes.json`, the Python API, or the frontend's advanced mapping controls. Names must match supplied dependency files (absolute paths are also accepted by the API/CLI). Same-named procedures can have different definitions in different explicit scopes. Contradictory definitions within one scope are rejected.

With multiple metadata databases, an unscoped definition is assigned only when an existing object identifies a unique context. Otherwise DEPENDENCY_SCOPE_REQUIRED is recorded; the definition is not broadcast to every database. Missing, unknown, and ambiguous caller connections never fall back to a procedure from another connection. Nested calls retain the caller's scope. Cross-database calls need review when the database identity differs. SQL Server execution-principal-specific name resolution is not claimed.

## Task graph

`task_graph.json` records source IDs, generated step IDs, source constraints, edges, topological order, virtual sequence completion nodes, and cycles. Every parsed task stays represented.

Success-AND dependencies are supported. A sequence's outgoing dependency begins after all its children complete. Disabled containers also disable descendants. Event handlers retain a distinct event scope and do not create false normal-branch concurrency findings. Their execution semantics remain unimplemented. Loops, required transactions, checkpoints, forced outcomes, expressions, failure/completion/OR edges, malformed endpoints, and cycles block execution instead of being flattened.

Independent branches are preserved in the graph. A single notebook would serialize them and alter failure isolation, so PARALLEL_CONTROL_REVIEW_REQUIRED blocks executable acceptance until pipeline concurrency/error semantics are agreed. The Jobs generator respects graph dependencies and rejects missing/cyclic dependencies; it does not implicitly chain graph tasks or deploy anything.

## Native data-flow subset

- Static mapped OLE DB sources with captured unique output columns.
- Multicast, preserving each output port identity.
- Restricted Derived Column expressions: column copies, literals, Boolean/comparison/null expressions. Casts, arithmetic with unresolved type behavior, custom functions, variables, and ternaries are blocked. Declared type/length/overflow behavior still needs review.
- Conditional Split with explicit rule order, first-match exclusion, and a distinct default output. Unknown order is blocked. NULL condition results fail explicitly instead of being silently routed to default.
- Mapped OLE DB destination previews. LOAD_STRATEGY_REQUIRED blocks writes until append/replace/merge/key/rerun semantics are agreed. No write strategy is selected automatically.

Component order in XML is not data-flow order. Edges resolve exact output/input IDs; disconnected, duplicate, cyclic, or unresolved endpoints are findings. A failing component's output is not reused downstream. Multi-input joins, lookups, aggregates, Script components, row-error redirection/ignore policies, and unsupported expressions remain explicit blockers. Both `FailComponent`/`RD_FailComponent` and `NotUsed`/`RD_NotUsed` disposition spellings are recognized. Ignore, redirect, and unknown values remain blocked. No UDF or null/pass-through substitute is silently introduced.

Graph generation is deterministic. The existing AI fallback is available in legacy mode; graph gaps are not sent to an LLM to invent missing bindings, routing, or write semantics.

## Evidence and verification

Additional artifacts:

- `dataflow_graphs.json`: component and port edges, order, mapping uses.
- `dependency_inventory.json`: connection/database identities, aliases, procedure availability/hashes/origins, nested references.
- `verification_plan.json`: proposed schema/key/null/routing/type/write/failure comparisons, marked NOT_EXECUTED.

The verification plan is a requirements artifact. No Spark, legacy workload, dataset comparison, deployment, or measured accuracy is implied. Routing tests include an in-memory contract harness, which does not replace real Spark/SSIS testing.

## Compatibility

Existing single-file dependency arguments, artifact review protection, and the no-UDF policy remain supported. Graph strict mode now writes blocked review drafts; normal assessment statuses remain separate from notebook and execution-readiness status. The default graph engine may generate fewer legacy warehouse-specific operations while identifying unsupported semantics more precisely. Use legacy mode for controlled comparison, not as evidence of equivalence.

Further work: decide pipeline concurrency/failure handling and data-flow write/load strategies with the user; add typed bindings, more native transforms, optional reference guidance, authorized Volume intake, review-state management, and actual comparison evidence.
