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

## Physical mappings

```json
{
  "source_connection|dbo.Customer": {"target": "catalog.bronze.customer", "layer": "bronze"},
  "target_connection|dbo.Customer": {"target": "catalog.silver.customer", "layer": "silver"}
}
```

Keys identify legacy objects. The optional prefix is the exact logical connection reference recorded in package IR/dependency inventory. A connection-scoped key takes precedence over an unscoped key. An unscoped key intentionally maps that object across all connections; it is not a fallback to a guessed schema. Unqualified legacy objects use `default_schema`.

Every physical table in supported SQL must have an explicit mapping. CTE aliases remain local query names. Targets must be three-part Databricks identifiers; expressions, extra SQL, and target aliases are rejected. Source aliases are preserved, including a renamed physical source table when its original qualifier is used. Database-qualified column references, embedded-dot source identifiers, table-valued/custom functions, temporal/hinted access, unsupported statements, and unbound parameters remain review requirements.

A target-schema hint is not a mapping. For a single-layer package it must agree with mapped write targets. Multi-layer packages require an explicit layer on each output mapping. Read/source mappings can belong to a different layer from the package's output layer. Notebook catalog/schema widgets cannot silently override a mapping version; changing them requires regeneration.

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

Success-AND dependencies are supported. A sequence's outgoing dependency begins after all its children complete. Disabled containers also disable descendants. Event handlers, loops, required transactions, checkpoints, forced outcomes, expressions, failure/completion/OR edges, malformed endpoints, and cycles block execution instead of being flattened.

Independent branches are preserved in the graph. A single notebook would serialize them and alter failure isolation, so PARALLEL_CONTROL_REVIEW_REQUIRED blocks executable acceptance until pipeline concurrency/error semantics are agreed. The Jobs generator respects graph dependencies and rejects missing/cyclic dependencies; it does not implicitly chain graph tasks or deploy anything.

## Native data-flow subset

- Static mapped OLE DB sources with captured unique output columns.
- Multicast, preserving each output port identity.
- Restricted Derived Column expressions: column copies, literals, Boolean/comparison/null expressions. Casts, arithmetic with unresolved type behavior, custom functions, variables, and ternaries are blocked. Declared type/length/overflow behavior still needs review.
- Conditional Split with explicit rule order, first-match exclusion, and a distinct default output. Unknown order is blocked. NULL condition results fail explicitly instead of being silently routed to default.
- Mapped OLE DB destination previews. LOAD_STRATEGY_REQUIRED blocks writes until append/replace/merge/key/rerun semantics are agreed. No write strategy is selected automatically.

Component order in XML is not data-flow order. Edges resolve exact output/input IDs; disconnected, duplicate, cyclic, or unresolved endpoints are findings. A failing component's output is not reused downstream. Multi-input joins, lookups, aggregates, Script components, row-error redirection/ignore policies, and unsupported expressions remain explicit blockers. No UDF or null/pass-through substitute is silently introduced.

Graph generation is deterministic. The existing AI fallback is available in legacy mode; graph gaps are not sent to an LLM to invent missing bindings, routing, or write semantics.

## Evidence and verification

Additional artifacts:

- `dataflow_graphs.json`: component and port edges, order, mapping uses.
- `dependency_inventory.json`: connection/database identities, aliases, procedure availability/hashes/origins, nested references.
- `verification_plan.json`: proposed schema/key/null/routing/type/write/failure comparisons, marked NOT_EXECUTED.

The verification plan is a requirements artifact. No Spark, legacy workload, dataset comparison, deployment, or measured accuracy is implied. Routing tests include an in-memory contract harness, which does not replace real Spark/SSIS testing.

## Compatibility

Existing single-file dependency arguments and artifact review protection remain supported. Status semantics and original no-UDF policy remain intact. The default graph engine may generate fewer legacy warehouse-specific operations while identifying unsupported semantics more precisely. Use legacy mode for controlled comparison, not as evidence of equivalence.

Further work: decide pipeline concurrency/failure handling and data-flow write/load strategies with the user; add typed bindings, more native transforms, optional reference guidance, authorized Volume intake, review-state management, and actual comparison evidence.
