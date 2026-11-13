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

When procedure files arrive, regenerate. Dependency hashes create a new output version and reviewed outputs remain protected. A pure EXEC can replace its pending section when its definition fits the supported relational subset. Optional outer BEGIN/END wrapping, literal/default arguments, positional arguments followed by named arguments, and named input task bindings are supported. Arguments are matched to declared formal names and cast before use; task binding names need not match those formals. Only the final query of a bounded body may supply ordinal single-row results. Output/return parameters, unused formals, declarations, nested EXEC, execution options, table-valued/user-defined types, and general procedural bodies remain pending. Definition availability alone is not translation success. No UDF is generated.

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

Success-AND dependencies are supported on the main path. A sequence's outgoing dependency begins after all its children complete. Disabled containers also disable descendants. Event handlers retain a distinct scope and do not create false normal-branch concurrency findings. A flat ordered package OnError handler is supported as described below. Loops, required transactions, checkpoints, forced outcomes, main-path expressions/failure/completion/OR edges, malformed endpoints, and cycles remain blockers.

Independent branches are preserved in the graph. A single notebook would serialize them and alter failure isolation, so PARALLEL_CONTROL_REVIEW_REQUIRED blocks executable acceptance until pipeline concurrency/error semantics are agreed. The Jobs generator respects graph dependencies and rejects missing/cyclic dependencies; it does not implicitly chain graph tasks or deploy anything.

## Active SQL and variable provenance

OLE DB source AccessMode selects the evidence used by generation and analysis: 0 = OpenRowset, 1 = OpenRowsetVariable, 2 = SqlCommand, 3 = SqlCommandVariable. Unused properties can contain old designer text; that text is retained in the raw IR/pending reference comments but cannot supply active dependencies, result-set types, or load-strategy evidence.

Variable-backed table sources and unsupported SQL expressions remain pending. A SQL expression made only of literal-string concatenation and scalar references can be compiled when its scoped declaration has EvaluateAsExpression enabled. Each reference must occupy a SQL value position; quoted strings, comments, and dynamic identifiers are rejected. Bare numeric fragments accept only numeric runtime values and are bound through Spark SQL arguments. Their draft sections include the closest lexical variable declaration when available, scope, captured value, expression, EvaluateAsExpression flag, and referenced runtime variables/package/project parameters. Sensitive declarations are redacted in this added context. Missing scope in older IR is identified as SCOPE_UNAVAILABLE; duplicate declarations are AMBIGUOUS. Captured snapshots are not substituted for runtime expressions. The supported template is parsed and mapped before runtime; unsupported expressions are not executed. Declared expressions with evaluation disabled do not create runtime-reference requirements.

Same-named variables in unrelated containers are not visible. XML ancestry retains event-local scope even when executable IDs are opaque. References inside quoted expression literals are not treated as variable bindings. Variable-based Execute SQL Tasks also retain declaration context. Arbitrary expressions, provider-specific conversions, full result sets, procedure output parameters, and state transfer across separate Job tasks remain future work.

Query-template references resolve from the SQL variable's declaration scope. A package-level expression does not pick up a task-local shadow merely because that task reads the expression. This rule is shared by data-flow variable queries and variable Execute SQL Tasks.

Computed scalar variables have a separate bounded compiler: literals, explicit references, typed string/numeric addition, parentheses, numeric negation, DT_I2/I4/I8/R4/R8/BOOL/DATE/DBTIMESTAMP casts, and SUBSTRING/LEFT/RIGHT. String slicing counts UTF-16 units. Expressions run as ordinary Python scalar helpers at each read, so later result assignments are visible. There is no eval, cached designer substitution, Spark UDF, or manual override of computed logic. Cycles, unknown functions/types, ambiguous additions, and result assignments into computed variables remain gaps. Source type/NULL/date conversion semantics still require review.

Expression Tasks use this compiler for one assignment to a declared User variable. Captured source assignments update shared state before later tasks read it. Writes into computed variables, unknown destinations, multiple assignments, and unsupported functions remain blocked. Pending items retain `source_expression`, including cutoff expressions using GETDATE/DATEADD/DATEPART. No source clock/timezone policy is inferred.

## Native input and result bindings

Execute SQL Tasks support unique named input bindings, input direction only, and ordinal single-row results. A bounded batch contains at most eight supported relational statements; only its final query may supply a result. The specific INSERT VALUES followed by SELECT shape can be recovered when SQL Server omits the separating semicolon. DECLARE, general EXEC, output/input-output parameters, and ambiguous result ordering remain pending.

Generated `spark.sql(literal_sql, args={...})` calls bind values without inserting them into SQL text. Result collection is limited to two rows so zero/multiple-row responses fail before assignment. Ordinal bounds and scalar conversions are checked. SSIS variable types are used where captured; provider, NULL, precision, and naive-datetime timezone behavior still need review. Captured non-sensitive package parameters supply defaults only when not marked Required. Required/sensitive package parameters, project parameters, and environment-specific system inputs must be supplied explicitly. Package identity/version properties are retained from owned metadata rather than inferred from the developer's machine.

Use the notebook `ssis_runtime_values` JSON widget. Keys and types are listed in the notebook, assessment, and UI. Example for a synthetic package using these declared inputs:

```json
{"$Package::BatchKey": 20261110, "System::UserName": "test-user", "System::MachineName": "test-runner"}
```

All bound operations share one notebook process (`step_id=all`). Separate per-step Job generation is rejected because Python variable state does not transfer between notebook runs. This does not choose a deployment or orchestration strategy.

For the recognized row-count dynamic SQL shape, a captured table declaration is parsed and mapped at generation time, and its runtime value must remain unchanged. Other dynamic SQL remains pending. An INSERT/SELECT batch emits separate SQL calls with an atomicity warning. INSERT followed by MAX receives RESULT_KEY_GENERATION_REVIEW_REQUIRED because target-generated keys and concurrent writers must be designed explicitly. No identity or key-allocation implementation is invented.

## Variable Execute SQL Tasks

`SqlStatementSourceType=Variable` (or `2`) now has a bounded native implementation. The property must resolve to one scoped, non-sensitive String variable, and the task must declare explicit ordinal single-row result bindings. This path supports one relational query, not write SQL or general procedural execution.

Two declaration forms are supported:

- **EvaluateAsExpression=true:** literal strings concatenated with numeric scalar references, for example `"SELECT COUNT(*) FROM dbo.Orders WHERE batch_key=" + @[$Package::BatchKey]`. The template is parsed/mapped once and value references become named Spark SQL arguments at each read. Extra Execute SQL task parameter bindings are rejected in this form. Captured designer snapshots are ignored.
- **EvaluateAsExpression=false:** a captured query such as `SELECT COUNT(*) FROM dbo.Orders WHERE batch_key=@key`. Supported named task input bindings provide values. The generator translates the captured text; it never passes the runtime string to `spark.sql`. Initial preflight and a task-level guard reject changed query-text overrides. Known source tasks writing that captured SQL variable produce `VARIABLE_SQL_MUTATION_UNSUPPORTED` instead of silently freezing changing source behavior.

Example task properties for a captured query:

```json
{
  "SqlStatementSourceType": "Variable",
  "SqlStatementSource": "User::CountQuery",
  "ResultType": "ResultSetType_SingleRow",
  "parameter_bindings": [
    {"ParameterName": "key", "DtsVariableName": "$Package::BatchKey", "ParameterDirection": "Input"}
  ],
  "result_bindings": [
    {"ResultName": "0", "DtsVariableName": "User::RowCount"}
  ]
}
```

The package must also declare the SQL and result variables. Conversion requires available scoped declarations, supported query syntax, and resolved physical locations. Output metadata records the active variable reference/scope, source hash, query kind, two-row limit, and result bindings as `VARIABLE_TASK_BINDING_REVIEW` evidence. Successful translation removes the generic missing-static-SQL dependency finding for that task alone. Rejected forms keep their variable context in pending evidence; missing procedure bodies are not implemented by this feature.

Results use the same bounded cardinality, ordinal, and source-type checks as direct Execute SQL Tasks. They can feed later ordered SQL tasks within one notebook. Additional result modes, runtime query overrides, task/file-based SQL expressions, SQL-structure concatenation, positional/provider-specific bindings, procedures, and write/batch SQL remain review work. No source SQL, variable expression, or reference notebook is evaluated as Python, and no Spark/Pandas UDF is introduced.

## Native data-flow subset

- Static mapped OLE DB sources with captured unique output columns.
- Multicast, preserving each output port identity.
- Restricted Derived Column expressions: column copies, literals, Boolean/comparison/null expressions. Casts, arithmetic with unresolved type behavior, custom functions, variables, and ternaries are blocked. Declared type/length/overflow behavior still needs review.
- Conditional Split with explicit rule order, first-match exclusion, and a distinct default output. Unknown order is blocked. NULL condition results fail explicitly instead of being silently routed to default.
- Mapped OLE DB destinations use `destination_write_mode=source` by default. Captured AccessMode 0 or 3 on authoritatively identified table/FastLoad destinations preserves INSERT behavior. Missing/unknown modes retain LOAD_STRATEGY_REQUIRED. A separate source TRUNCATE stays separate and ordered; no overwrite, extra truncate, MERGE, or table creation is added.

Native destination writes require a static mapped location, one explicit input, exact source lineage to external metadata IDs, and unique target columns. Existing target columns and source/target types must exactly match captured metadata. Projections follow the actual target schema order before Spark's positional insertInto call. Target non-null and captured WSTR UTF-16 lengths receive native row checks. ANSI/code-page conversions, unsupported types, missing mappings, extra/default/identity target columns, and incompatible schemas fail for review. Fast-load commit, target constraints, rerun safety, and transactional differences remain review items. `--destination-write-mode review` retains projections and pending writes. `append_existing` explicitly requests the guarded insert path; unsupported access modes and dynamic targets still remain blocked.

Component order in XML is not data-flow order. Edges resolve exact output/input IDs; disconnected, duplicate, cyclic, or unresolved endpoints are findings. A failing component's output is not reused downstream. Multi-input joins, lookups, aggregates, Script components, row-error redirection/ignore policies, and unsupported expressions remain explicit blockers. Both `FailComponent`/`RD_FailComponent` and `NotUsed`/`RD_NotUsed` disposition spellings are recognized. Ignore, redirect, and unknown values remain blocked. No UDF or null/pass-through substitute is silently introduced.

Graph generation is deterministic. The existing AI fallback is available in legacy mode; graph gaps are not sent to an LLM to invent missing bindings, routing, or write semantics.

## Package error handling and notifications

A flat package-level OnError handler with one ordered source DAG is emitted as an ordinary Python function and invoked only when a main notebook step raises. Handler tasks are not run as normal pipeline steps. Supported handler edges preserve Success, Failure, Completion, Expression, ExpressionAndConstraint, ExpressionOrConstraint, and uniform incoming AND/OR conditions. Skipped predecessors do not trigger downstream constraints. Nested handlers, containers, loops, independent branches, mixed enabled/disabled tasks, mixed AND/OR groups, required transactions, and forced outcomes remain blockers.

System::SourceName/SourceDescription/ErrorDescription come from the failing main task and exception. The notebook `ssis_error_codes` JSON widget maps a target exception condition/class to an explicitly reviewed source numeric audit code. An unmapped code is unavailable, rather than replaced with an invented value or reused from an earlier failure. Handler failures are recorded in `_ssis_event_errors`; the original main exception is re-raised even if handler dispatch fails. This runs once per failed main step. SSIS event bubbling, original numeric codes, provider details, and failure propagation still need review.

Binding metadata marks these failure-context values separately from manual runtime inputs. ErrorCode comes from `ssis_error_codes`, not a JSON override in `ssis_runtime_values`. Values assigned through source result bindings carry their producer references even when those producer implementations are pending. `required_external_input` excludes defaults, derived expressions, produced results, and failure context. A source result marker does not implement a missing procedure. Captured audit-count defaults retain their literal values; measured counts are not invented.

Actual Send Mail Tasks support captured SMTP host/port, optional STARTTLS, direct/one-variable headers and body, To/Cc/Bcc recipients, and priority. Credentials are not extracted; Windows authentication, attachments, file-backed bodies, and compound property expressions remain pending. Notification transport/network access and TLS trust require target review. Automated tests use a fake SMTP transport and send no messages.

A Script Task named “Send Mail Task” remains a Script Task. The parser extracts available plain/Base64 UTF-8/UTF-16 C#/VB project source and retains only a hash for compiled binaries. One bounded C# mail subset is translated from a captured ScriptMain.cs entry point: string constants/concatenation, string or signed-integer Dts variable ToString, literal SMTP connection-host reads, MailMessage/MailAddress/SmtpClient creation, headers, HTML/plain bodies, nonempty-literal Replace, recipient collections, simple optional-header conditions, and one unconditional Send followed by Success. The generated code uses Python's email/smtplib libraries, never a Spark/Pandas UDF.

The entire ScriptMain file must match the supported namespace/class/result-enum/constants/Main structure. Unknown calls, extra methods/constructors/fields, unsupported conditionals, multiple sends, arbitrary host expressions, additional SMTP properties, and missing/ambiguous connection metadata remain review gaps. Captured ReadOnlyVariables/ReadWriteVariables declarations must cover referenced variables. Additional/uninspected project source files remain blocked; known generated settings/resource/assembly files are retained as source evidence. Source text is parsed as data, never executed or dynamically evaluated. Supporting generated resource files are not translated into runtime behavior.

For a source `new SmtpClient(host)` that reads only SmtpServer, the generated transport uses the constructor's port-25/plain defaults; it does not copy unrelated SSIS connection TLS/authentication settings. Review .NET application/machine SMTP defaults, culture/encoding/address semantics, target relay access, and notification delivery. Bcc recipients remain envelope-only; HTML body/template transformations are preserved. Synthetic tests use a fake SMTP transport and send no messages. Other captured scripts remain named translation gaps, and compiled/absent programs cannot be reconstructed from labels.

## Evidence and verification

Target audit mode replaces the recognized source audit profile with an adapter for existing `audit_notebooks`/`audit_tables`. The frontend selects it by default; the CLI/API retain explicit source/target selection. See the README target-audit contract for location settings, execution_key attachment, statistics, and business key bridges. Replaced source statements remain in `audit_replacement_plan.json`; they are not executed or counted as converted business work. Unknown recovery or embedded source-audit writes remain blocked. Source-mode handler/notification behavior above is retained for comparison.

Additional artifacts:

- `dataflow_graphs.json`: component and port edges, order, mapping uses.
- `dependency_inventory.json`: connection/database identities, aliases, procedure availability/hashes/origins, nested references.
- `verification_plan.json`: proposed schema/key/null/routing/type/write/failure comparisons, marked NOT_EXECUTED.

The verification plan is a requirements artifact. No Spark, legacy workload, dataset comparison, deployment, or measured accuracy is implied. Routing tests include an in-memory contract harness, which does not replace real Spark/SSIS testing.

## Compatibility

Existing single-file dependency arguments and the no-UDF policy remain supported. Manifest hashes now protect unmarked notebook edits in addition to explicit review markers; unreadable/untracked outputs are preserved for revalidation. Graph strict mode now writes blocked review drafts; normal assessment statuses remain separate from notebook and execution-readiness status. The default graph engine may generate fewer legacy warehouse-specific operations while identifying unsupported semantics more precisely. Use legacy mode for controlled comparison, not as evidence of equivalence.

Further work: extend native components, script and procedural semantics, provider/type coverage, concurrency and transaction designs, optional reference guidance, authorized Volume intake, review-state management, and actual comparison evidence. Source-preserving inserts do not settle target identity, rerun, or broader pipeline strategy.
