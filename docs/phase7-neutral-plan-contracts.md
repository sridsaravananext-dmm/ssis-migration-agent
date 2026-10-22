# Phase 7, first increment: neutral behavior bound to plans and contracts

## Shared archive review

The user-provided archive was retrieved from `GenericAITool-version01` at
commit `6d5c939e9a5d22a8c71f3a0104bf628f01f2daa6`, path `tsql_plsql 1.zip`.
Its SHA-256 is
`da39023361d595d6191af3a572ea549724f3a07386c01c356fc6bdccc4f6b210`.
It contains 3,510 entries, mostly a bundled Windows environment, plus a small
parser prototype, tests and two SQL samples. Only source/test text was read
for review; the bundled environment was not installed or run. The remote
revision was fetched without merging or replacing the ongoing local work.

Useful material includes the two representative SQL fixtures and the emphasis
on source hashes, statement evidence, procedural boundaries and unresolved
logic. The samples are preserved byte-for-byte under `examples/shared_sql/`,
with per-entry hashes and archive provenance. Regression tests keep their
procedural/transaction/exception/dynamic-SQL behavior explicitly unverified.

| Prototype area | Decision |
| --- | --- |
| T-SQL/PL-SQL samples | Reuse as offline regression inputs, not accepted translations. |
| Source identity and consistent IR records | Reuse the approach through existing project schemas and hashes. |
| Batch/block delimiters and raw evidence | Useful requirements for a later procedural-language phase. |
| `parsed = any(expression is not None ...)` | Do not adopt as support/acceptance proof; opaque SQLGlot fallbacks can pass it. |
| Regex dependency/alias extraction | Do not make it authoritative lineage or semantic evidence. |
| Line-based GO/slash splitting | Requires lexical/context validation before adoption; quoted/commented delimiters must not split code. |
| `parse_sql_file()` positional/filename handling | Needs repair before reuse: its callee can interpret the filename as a dialect. |
| API server and packaged environment | Not imported; the current project keeps its existing interfaces and dependencies. |

## Why binding was needed

Phase 5 made a neutral structural graph available, but contracts still listed
only the legacy planner's inferred behaviors. A task omitted by that planner
could therefore be absent from weighted acceptance accounting. This increment
binds the existing plan to the complete neutral graph while preserving its
step count, migration intent, strategy decisions and dependency lists.

The SSIS adapter now reads executable containment from actual parentage in the
already-loaded DTSX tree. It adds typed `CONTAINS` edges and keeps the extracted
relationships in source extensions. Names and refId string prefixes do not
establish parentage. The old parser dictionary, `PackageIR` and analysis
outputs remain unchanged. The neutral graph gets richer; its portable golden
fixture is updated accordingly.

`bind_plan_to_ir()` resolves each step's explicit `source_node_id`, or its
existing native `metadata.ref_id` by exact identity. It follows structural
containment/expression links, never matching display names. Missing roots,
overlapping scopes and missing task hierarchy remain review findings.

Every neutral node and edge is allocated **exactly once** as a preservation
obligation. Shared resources have `PACKAGE_SHARED` scope. Unplanned or ambiguous
behavior has `PACKAGE_REVIEW` scope, stored on a deterministic review carrier
step. The carrier is an accounting location, not an implementation owner or
execution-order inference. The missing implementation remains unresolved;
putting the source record into a contract does not implement it.

Cross-step control edges are reported, including missing declared predecessor
steps and conditional control semantics requiring review. Dependencies are not
invented, and an ordinary Databricks job dependency does not establish that a
source expression/failure/transaction condition was reproduced.

## Contracts, validation and reporting

The existing `MigrationContract` and `migration_intent` remain. A bound step
gets a source scope under `metadata.normalized_source`. Its unchanged intent
is still preserved in the legacy snapshot. Contracts additionally preserve
`normalized_node` and `normalized_edge` obligations, including exact source
records and their evidence. Criticality/weights remain null and every behavior
remains `NOT_VERIFIED`. These extra kinds extend the existing version-1 schema;
older contracts without neutral scopes remain valid.

`validate_plan_ir_binding()` recomputes the whole binding against the graph.
It rejects omitted/changed records or forged/stale summaries, even if a caller
rebuilds a contract hash after altering a scope. Contract validation separately
checks lossless preservation of those obligations, evidence IDs, platform and
snapshot binding before generators consume them.

The runner writes `plan_ir_binding.json`, links it as `artifacts.plan_ir_binding`
and records a `normalized_plan_binding` stage before target generation. The
stage establishes completion of structural binding, not semantic acceptance.
The acceptance loader verifies the binding against the current graph, compares
the separate report with the plan, and hashes it into the evidence inventory.
Old packages without this feature remain readable. Changed packages and
contracts require renewed policy/evidence review.

`generate_migration_artifacts(..., normalized_ir=...)` is additive. Standalone
legacy calls without that argument keep their previous behavior. No new
provider, service, database connection or dependency was introduced.

## Limits and next increment

This is a neutral-source preservation bridge around the existing planner,
not a replacement planner or proof of behavioral completeness. Graph nodes
can still reflect incomplete upstream extraction. Unknown source tasks and
unplanned behavior stay blocked even if code was generated. Source weights,
critical rules, execution and data equivalence still require verified evidence.

The subsequent [C# assessment increment](phase7-csharp-assessment.md) adds offline lexical triage;
at the completion of this first increment, C# assessment was still pending, and
this change does not claim C# parsing or translation. Existing unknown script
behavior is retained as source obligations and review findings. Full PL/SQL
procedure/package semantics, robust SQL block splitting and dynamic-SQL
translation also remain pending. The shared SQL samples make those future
requirements testable without connecting to or running against a legacy server.

## Changed files

| File | Purpose |
| --- | --- |
| `adapters/ssis/hierarchy.py`, `adapter.py`, `normalizer.py` | Actual XML containment, without changing legacy IR semantics. |
| `migration/normalized_plan.py`, `migration/__init__.py` | Complete graph-to-plan binding and independent conservation checks. |
| `migration/contract_bridge.py`, `schemas/migration_contract.v1.json` | Preserve scoped neutral obligations in existing contracts. |
| `migrate.py` | Bind before generation and publish the binding artifact/stage. |
| `validator/acceptance.py` | Verify/hash the binding when present. |
| `examples/shared_sql/` | Original SQL fixtures, source provenance and usage note. |
| `tests/migration/test_normalized_plan.py`, `tests/test_neutral_contract_pipeline.py` | Binding, conservation, ambiguity, evidence and fixture regressions. |
| `tests/ir/fixtures/wwi_structural_summary.json` | Additional containment edges from actual source hierarchy. |
| `tests/adapters/test_ssis_adapter.py` | Update parser test double to expose the existing parser root used for containment. |

Source paths are relative to `src/ssis_migration/`, except examples/tests.

## Verification

- Full suite: **522 passed, 4 live integration tests skipped**.
- The unchanged legacy parse/IR/analysis regression passes; the planner still
  emits the existing 13 sample steps.
- The sample's richer neutral graph contains 192 nodes and 487 edges. All 679
  graph records occur exactly once across contracts, alongside the previous
  130 legacy obligations: 809 total unverified obligations.
- Four unplanned nodes are retained explicitly, including package-level
  date-dimension preparation. They are review obligations, not implemented
  behavior. They remain blockers rather than being excluded from accounting.
- A built wheel imported outside the checkout completed generation, verified
  graph/plan binding and loaded the expanded acceptance inventory. Acceptance
  remains false; execution remains `NOT_RUN`.
- Both shared SQL files match their archive entry SHA-256 hashes. Their
  unresolved procedural behavior remains explicit in language-analysis tests.
- No SQL sample, generated workload, deployment, live AI call or bundled
  prototype environment was executed. Diff checks passed.
