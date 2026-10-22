# SSIS correctness baseline, result version 1.0

Phase 1 preserves the SSIS parser → `IRBuilder().build(parsed)` → analyzer
flow, `migration_intent`, provider interfaces and the `run_migration` signature.
Databricks remains the only target. Other source adapters and verified weighted
semantic acceptance are later phases.

From the repository root:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ssis_migration.migrate \
  --source examples/wwi/DailyETLMain.dtsx \
  --output /tmp/ssis-phase1 \
  --connection-mapping examples/wwi/connection_mapping.json \
  --base-path "$PWD"
.venv/bin/python -m streamlit run app.py --server.port 8000 --server.headless true
```

These commands generate files and perform static checks. They do not run target
transformations or deploy a bundle. Both CLI and Python API reject an execution
request. Live Databricks tests remain optional and require usable credentials.

## Readiness and compatibility

The existing `status`, `migrated_package_count` and package `MIGRATED` fields are
retained for callers. They describe **artifact generation only**. A legacy
`MIGRATION_COMPLETE_DEPLOYMENT_PENDING` value is not migration acceptance.
Use the additive fields in the run report, package manifest and `readiness.json`:

| Field | Meaning |
| --- | --- |
| `result_version` | Version of these additive result fields: `1.0`. |
| `run_id` | Identifies the current run and its package manifests. |
| `lifecycle_status` | `BLOCKED` for unresolved/rejected/review findings; otherwise `VALIDATED` within `STATIC_ONLY` scope. |
| `acceptance.accepted` | Always `false` in this phase. Syntax and generation do not establish business equivalence. |
| `acceptance.status` | `BLOCKED` with known findings; otherwise `NOT_EVALUATED`. |
| `coverage.structural` | Counts static validation records, not source business rules. |
| `coverage.semantic` | `NOT_VERIFIED`; percentage is unknown (`null`). |
| `coverage.execution`, `coverage.data_equivalence` | `NOT_RUN`; no row comparisons or target execution are claimed. |
| `blocking_issues` | Findings to resolve before controlled execution or acceptance. |

`stages.static_validation == SUCCEEDED` means checkers completed, not that every
finding passed. Inspect `validation_results.json`. `stages.data_validation` and
`execution_validation` remain `NOT_RUN`. The required semantic threshold is
recorded as 80%, but weighted coverage and critical business-rule verification
are not implemented or inferred from artifact counts.

## Outputs and validation

Each package emits IR, analysis, plan, manifest, readiness and scoped validation
JSON; generated code/metadata/operation history; a guarded notebook; job JSON;
DAB configuration; and a human review report. DAB output uses JSON syntax,
which is valid YAML, to preserve quoting without adding dependencies. Compute
defaults still need workspace-specific review before deployment.

Existing nonempty package directories are preserved. Reruns and colliding
package basenames use a new suffixed package directory; the current run report
contains the exact paths. Earlier manifests cannot masquerade as a failed
refresh's output.

Only the active notebook code path gates static readiness. When independently
checked AI code replaces a whole step, earlier deterministic operations remain
in `*_operations.json` as history. Embedded SQL in active Python code is checked
independently; dynamic SQL remains review-required. Invalid graphs, unknown
dependencies and task-key collisions cannot produce executable job graphs.
Incomplete or statically rejected steps receive a runtime guard before any
operation in that step, including staging writes.

Temporal query detection records original procedure text offsets. It does not
supply archive names or period columns. Missing historical-data prerequisites
remain unresolved, and a model cannot remove those findings. Unresolved AI
responses and language mismatches cannot replace the pending artifact.

The assembler no longer supplies WWI control-table behavior automatically.
Callers that have independently verified utility code may supply
`plan.metadata.control_helpers = {"code": "...", "evidence": [...], "verified": true}`.
Referenced helpers without that evidence block the step. This is an explicit
caller evidence assertion, not automatic semantic verification.

Remaining work includes source-neutral intake/adapters, operational IR/contract
schemas, full dependency completeness, incremental inference/type mapping
corrections, weighted semantic coverage and controlled execution/reconciliation.
No support for TM1, PowerCenter, BODS or ODI is claimed by this phase.

## Changed components and verification

| Files | Reason |
| --- | --- |
| `src/ssis_migration/migrate.py` | Restore artifacts, preserve per-run evidence, validate active code paths and report scoped readiness. |
| `src/ssis_migration/validator/readiness.py` | Summarize independent static findings without granting semantic or runtime acceptance. |
| `src/ssis_migration/agent/context_builder.py`, `planner.py`, `prompt_builder.py` | Preserve supplied database evidence and remove invented temporal metadata. |
| `src/ssis_migration/agent/code_validator.py`, `orchestrator.py` | Validate declared languages, reject mismatches and retain unresolved prerequisites. |
| `src/ssis_migration/generator/notebook.py` | Guard incomplete/rejected steps, require evidence for utilities and validate graph/notebook structure. |
| `src/ssis_migration/generator/job_generator.py` | Preserve declared dependencies, parameters and complete DAB resource references. |
| `app.py` | Enable the bundled sample and show generation/readiness results correctly. |
| Relevant existing tests and new AI, notebook, readiness, pipeline and UI regressions | Cover actual regressions, failure propagation, history retention and execution guards. |

Verification of this phase: **177 passed, 4 live Databricks tests skipped**;
`git diff --check` passed. The CLI smoke run generated 13 step artifacts and a
13-task job; all generated Python notebook cells compiled. The Streamlit test
selected the WWI sample, generated its results and rendered the blocking state
without UI errors. The sample remains `BLOCKED` with unresolved translation and
control-helper evidence. Target workloads were not executed.

Valid public entry points remain compatible. Invalid dependency graphs now
raise `ValueError`; `NotebookAssembler.validate(notebook)` and the optional
`validate_generated_code(..., language=...)` argument are additive interfaces.
