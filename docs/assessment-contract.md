# Assessment contract and implementation boundary

This change is the first backend/application foundation from the comparative review. It uses independently written workflow concepts: common configuration, repeatable dependencies, structured findings, strict/dry-run modes, provenance, and reviewed-output protection. It does not copy or package third-party code.

## Evidence model

Source inventory contains one entry per parsed executable and data-flow component. `PLANNED_CANDIDATE` means associated with a legacy logical load, not implemented or verified. `REVIEW_ONLY` means retained for manual work. Missing sequence-based planning no longer removes tasks from the output plan.

`operation_coverage_percent` counts operations passing static native-code checks. The denominator includes missing/rejected implementations. AI replaces an operation group only when the orchestrator returns accepted generated code; assumptions, provider errors, and unresolved items remain findings. No task-equivalence or legacy-accuracy percentage is produced.

`verification.status = NOT_MEASURED` and `legacy_match_percent = null` until a future comparison harness supplies runtime evidence. Passing syntax checks cannot change these fields.

## Findings and status

Each finding records a stable identity, code, source ID, severity, message, evidence, and OPEN review state. Repeated source/issue combinations keep their IDs. Run IDs identify separate executions. A review-state editing/persistence UI is not yet implemented; stable IDs enable that later.

Missing procedure definitions, dynamic references, uninspected SQL, omitted source work, unsupported transformations/routing, non-default precedence, invalid code, unsafe native-code patterns, and unapplied physical mappings are explicit findings. Legacy naming/control-table assumptions remain warnings requiring review rather than inferred proof of equivalence.

A technical failure is separate from human review. Mixed package success/failure produces PARTIAL_FAILURE. Strict mode blocks notebook output for error findings but preserves diagnostics. Dry run skips code generation and AI and writes analysis/plan/review artifacts. Normal-mode incomplete notebooks cannot run through missing operations silently: rejected operation bodies become explicit runtime errors and global blockers stop execution before generated setup mutates data.

## Dependency resolution

Procedure definitions are loaded from all supplied files with UTF-8/BOM or UTF-16 BOM support. Comment/literal text is excluded from header/call discovery. Qualified names are normalized case-insensitively; unqualified names use the configured default schema. Conflicts across files or against metadata raise a descriptive input error. There is no any-schema first-match fallback.

Definitions enrich both the DTSX-derived and external-metadata context before planning, and nested references are included in dependency graph traversal. Missing nested definitions remain findings. DDL enrichment applies on both paths, including new tables. Conflicting supplied table definitions are rejected. Multi-database identities and SQL Server execution-principal resolution still require review when ambiguous.

## Artifact lifecycle

Version directories use source/dependency/configuration/engine content identity. Referenced connection metadata participates in the fingerprint. Different same-named batch packages receive distinct directory names. Input filenames and connections are retained in provenance. Timestamps and run IDs do not form finding identities.

An explicit reviewed marker protects output; unreadable existing notebooks are protected conservatively. Changed inputs create a new version. Deterministic versions are reassessed rather than trusting cached validation results. This provides predictable artifacts, not a promise that rerunning the generated data workload is idempotent.

The legacy Jobs generator now forwards its existing `schema` argument consistently in job and notebook parameters. The notebook treats it as context and blocks execution when it is supplied until explicit output mapping is implemented; it does not silently ignore or apply a schema rewrite. Job generation remains separate from deployment and from pipeline/load-strategy decisions.

## Remaining work and acceptance criteria

1. Replace legacy name-based load detection with graph-aware generation. Renaming a task must not change business semantics; independent sources and branch destinations must stay distinct.
2. Implement physical per-output target mapping. The current target-schema field records intent and raises review; it does not secretly rewrite source objects.
3. Add human review state management keyed by finding ID, with decisions tied to source versions and evidence.
4. Add runtime comparison harnesses for keys, duplicates, nulls, decimals, timestamps, temporal intervals, writes, cutoffs, reruns, and failure paths. Only measured evidence may populate verification results.
5. Add authorized Volume browsing and optional reference conventions using the same input manifest, without making a framework mandatory.
6. Decide pipeline strategy and load strategy with the user. Artifact generation, deployment, and data execution remain separate operations.

No broad workload-accuracy, memory, performance, or production-readiness claim is made by this implementation.
