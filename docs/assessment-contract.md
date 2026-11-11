# Assessment contract and implementation boundary

The first change established the backend/application foundation from the comparative review. It uses independently written workflow concepts: common configuration, repeatable dependencies, structured findings, strict/dry-run modes, provenance, and reviewed-output protection. It does not copy or package third-party code.

## Evidence model

Source inventory contains one entry per parsed executable and data-flow component. `PLANNED_CANDIDATE` means associated with a plan step, not implemented or verified. `REVIEW_ONLY` means retained for manual work. Missing sequence-based planning no longer removes tasks from the output plan.

`operation_coverage_percent` counts business operations passing static native-code checks. Container/control bookkeeping and intentional source-audit replacements stay validated but are excluded from the numerator and denominator; `structural_operation_count` and `audit_replaced_operation_count` report them separately. The denominator includes missing/rejected business implementations. AI replaces an operation group only when the orchestrator returns accepted generated code; assumptions, provider errors, and unresolved items remain findings. No task-equivalence or legacy-accuracy percentage is produced.

`generation.status = NOT_RUN` identifies a dry run; its zero operation counts mean checks were not performed. `planning.pending_section_count` counts pending sections discovered during planning, including dry runs. The UI and Markdown review show that count separately rather than implying that zero validated pending operations means no unfinished work. Result cards support the declared Streamlit 1.38 minimum without nested expanders.

`verification.status = NOT_MEASURED` and `legacy_match_percent = null` until a future comparison harness supplies runtime evidence. Passing syntax checks cannot change these fields.

## Findings and status

Each finding records a stable identity, code, source ID, severity, message, evidence, and OPEN review state. Repeated source/issue combinations keep their IDs. Run IDs identify separate executions. A review-state editing/persistence UI is not yet implemented; stable IDs enable that later.

Missing procedure definitions, dynamic references, uninspected SQL, omitted source work, unsupported transformations/routing, non-default precedence, invalid code, unsafe native-code patterns, and unapplied physical mappings are explicit findings. Legacy naming/control-table assumptions remain warnings requiring review rather than inferred proof of equivalence.

A technical failure is separate from human review. Mixed package success/failure produces PARTIAL_FAILURE. Graph mode always writes a non-dry-run review draft, including in strict mode. Named pending operations have validation status PENDING, never PASS, and runtime blockers remain. Strict CLI exits nonzero for review-required runs. Legacy strict mode still withholds incomplete notebooks. Dry run skips final notebook output and AI, and writes analysis/plan/review artifacts. Graph plans may include native-code previews for inspection. Normal-mode incomplete notebooks cannot run through missing operations silently: rejected operation bodies become explicit runtime errors and global blockers stop execution before generated setup mutates data.

## Dependency resolution

Procedure definitions are loaded from all supplied files with UTF-8/BOM or UTF-16 BOM support. Comment/literal text is excluded from header/call discovery. Qualified names are normalized case-insensitively; unqualified names use the configured default schema. Conflicts across files or against metadata raise a descriptive input error. There is no any-schema first-match fallback.

Definitions enrich both the DTSX-derived and external-metadata context before planning, and nested references are included in dependency graph traversal. Missing nested definitions remain findings. DDL enrichment applies on both paths, including new tables. Conflicting supplied table definitions are rejected. Multi-database identities and SQL Server execution-principal resolution still require review when ambiguous.

## Artifact lifecycle

Version directories use source/dependency/configuration/engine content identity. Referenced connection metadata participates in the fingerprint. Different same-named batch packages receive distinct directory names. Input filenames and connections are retained in provenance. Timestamps and run IDs do not form finding identities.

A generated notebook hash is stored in the manifest. Content changes protect unmarked manual edits; explicit reviewed markers, unreadable notebooks/manifests, and untracked outputs are also protected conservatively. Preserved outputs remain downloadable with REVALIDATION_REQUIRED and do not count as newly analyzed/generated packages. Changed inputs create a new version. Deterministic versions are reassessed rather than trusting cached validation results. This provides predictable artifacts, not a promise that rerunning the generated data workload is idempotent.

The legacy Jobs generator now forwards its existing `schema` argument consistently in job and notebook parameters. Legacy-mode notebooks treat it as context and block execution when it is supplied. Source-graph notebooks accept only the configuration used to generate target naming and overrides; changing runtime hints requires regeneration. Job generation remains separate from deployment and from pipeline/load-strategy decisions.

## Source-graph backend update

The default planner now builds identity-based task and data-flow graphs, emits supported native relational/DataFrame operations, applies explicit object overrides or visible catalog/schema naming proposals, and scopes supporting definitions by connection. Sequence completion barriers preserve outer dependencies. Original non-default precedence and unsupported runtime policies remain blockers. Graph-mode job definitions retain actual dependencies instead of imposing task-name ordering or a predecessor chain.

`pending_items.json`, `task_graph.json`, `dataflow_graphs.json`, `dependency_inventory.json`, and `verification_plan.json` make the backend decisions reviewable. The verification plan is a set of requirements, not evidence that comparisons ran. See [source-graph.md](source-graph.md) for the exact subset and limitations.

## Remaining work and acceptance criteria

1. Expand the identity-based graph translator to more component and runtime semantics. Renaming a task must not change business semantics; independent sources and branch destinations must stay distinct.
2. Validate physical mappings against the real target environment and extend column/type mappings. Per-object overrides are optional. Catalog/schema defaults propose lowercase table names; unknown locations and target-name collisions remain review blockers.
3. Add human review state management keyed by finding ID, with decisions tied to source versions and evidence.
4. Add runtime comparison harnesses for keys, duplicates, nulls, decimals, timestamps, temporal intervals, writes, cutoffs, reruns, and failure paths. Only measured evidence may populate verification results.
5. Add authorized Volume browsing and optional reference conventions using the same input manifest, without making a framework mandatory.
6. Extend pipeline, concurrency, transaction, and rerun strategies beyond the captured source inserts. A separate source TRUNCATE followed by an insert remains that sequence. Artifact generation, deployment, and data execution remain separate operations.

No broad workload-accuracy, memory, performance, or production-readiness claim is made by this implementation.

## Native binding implementation

Supported named inputs, ordinal single-row results, bounded relational batches, and literal/reference SQL templates generate executable native bodies. Runtime requirements remain separate from generation status. Missing procedures and unsupported handlers retain named pending sections. Identity allocation, statement atomicity, and source/target conversions retain explicit review findings. See [source-graph.md](source-graph.md) for the supported forms and shared-notebook restriction.

## Source-preserving development update

The graph planner now generates guarded inserts into existing mapped tables for captured OLE DB table/FastLoad destinations, matches bounded procedure arguments to formals, and evaluates supported scalar variable expressions from current runtime state. The generated scalar helpers are ordinary Python functions. Spark/Pandas UDFs remain prohibited.

Flat ordered package OnError handlers are dispatched only on main-step failure. They preserve supported handler constraints and retain the original exception if an audit/notification task fails. A warning records the once-per-step target event model. An explicit error-class mapping is required where a legacy numeric ErrorCode is consumed. Unsupported handler concurrency, grouping, transactions, nesting, and forced outcomes remain blockers.

Native Send Mail Task metadata supports a bounded SMTP translator; task names do not establish task type. Script Task programs are extracted as review data when present and remain source/translation gaps otherwise. Missing procedure definitions and missing scripts cannot count as implemented code. Having fewer pending sections does not remove identity-allocation, physical-mapping, or other execution-readiness findings, and does not change NOT_MEASURED behavioral match.

Captured ScriptMain.cs mail programs can now use the bounded parser/compiler documented in the source-graph guide. Supported source body/template/header/recipient logic generates native Python; unknown code stays pending. Script project access declarations are retained and checked. Tests use synthetic source and fake SMTP transports; private user packages are never committed as fixtures. Expression Tasks support bounded scalar assignments and retain unsupported expressions in pending evidence. Runtime bindings distinguish actual external inputs from source results and failure context. None of these changes adds business auditing, fills unavailable procedures, chooses shared physical identities, or turns literal count defaults into measured values.

## Target framework reference evidence

Reference inputs have a separate role from legacy procedures and source DDL. Their contents participate in manifest fingerprints, but their SQL/Python is never executed, sent through graph AI generation, applied to legacy schema metadata, or copied into a workload. The reference inventory retains archive/member identity, content hashes, bounded notebook metadata and contract findings. Imported notebook outputs, row literals, and credential values are excluded from its generated metadata. Ambiguous dynamic target identities remain review candidates.

Reference findings are warnings in source-preserving mode. They cannot make a missing legacy procedure available or count as implemented operations, and they do not change task coverage. A detected reference UDF or unbounded driver collection identifies a reuse incompatibility; keeping that file as evidence does not introduce those operations into generated code. Existing-framework auditing is not selected automatically by uploading a library. Database-definition and configuration archives with similar names remain separate inputs.

## Target audit replacement

The frontend now selects target auditing by default; API/CLI callers opt in through audit_mode=target. The native adapter writes the existing framework storage contract without importing the supplied library. This is an intentional target adaptation, not a claim that the old audit lifecycle is equivalent. All replacements retain source provenance in audit_replacement_plan.json, including SQL/bindings and business-consumed output values. Unknown recovery and mixed business/audit work remain pending instead of being discarded.

Target audit locations are explicit; runtime schema contracts precede business writes. New execution keys are positive UUID-derived Int64 values, and incoming execution_key values attach only to existing unique Running records. The target caller success protocol is supported without the source Boolean decoder. Native bound SQL preserves messages with quotes and uses optional job columns only when present. No DDL, UDF, blanket library import, or bulk driver collection is introduced. Table metrics use distributed counts and bounded Delta metadata lookups; failure logging preserves the original business exception. Shared state requires one notebook run.

Source classification and target storage have validated extension contracts; see [audit-configuration.md](audit-configuration.md). These handle different column signatures, connection-scoped objects, package-scoped explicit audit leaf tasks, physical table/column names, omitted optional roles, and lifecycle values. They preserve required role types and output bridges. Unknown workload semantics are still blockers. Package-root/event ownership is structural rather than tied to the literal root name `Package`. Frontend batches are selection-scoped and isolate duplicate filenames and per-package failures.

Source audit outputs still read by business code require explicit bridges. A target Int64 key cannot silently replace a source Int32 declaration. Unavailable business procedures remain pending. Known mapped writes receive statistics; outputs hidden in missing procedures are not guessed. Audit and data writes are not one atomic transaction. Target Delta updates/history, deployed schemas, concurrent key behavior, caller orchestration, and failure/rerun semantics require target-runtime verification; local Spark tests do not establish those results.
