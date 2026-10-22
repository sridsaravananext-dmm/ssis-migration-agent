# Phase 3: versioned migration-contract bridge

This phase extends the existing `agent.migration_contract.MigrationContract`
instead of adding a competing contract. The existing `migration_intent`,
planner output, SSIS IR and provider `generate(request, prompt)` interfaces
remain available. Standalone legacy generation calls without an attached
contract still work; the main pipeline always creates contracts.

The flow is now:

`parser → IR → analysis → context → planner → validated contract → generation → independent validators → notebook/job/report`.

Every contract is built and checked before the first target-code generator
runs. The package writes `migration_contracts.json` and links it from the
manifest/run report under `artifacts.contracts`. The plan stores the same
document in each step's `metadata.migration_contract`; generated artifact
metadata and operation records retain its contract ID.

## What the bridge records

Version `1.0` includes:

- A lossless snapshot of the original planner step and `migration_intent`.
- Explicit control-flow, data-flow, column-mapping, transformation and
  procedure-preservation obligations extracted from that existing snapshot.
- Input/output object references; schemas remain `UNVERIFIED`.
- Declared step dependencies, supplied/missing procedure definitions and file
  dependency results. Procedure references retain source/destination roles so
  a same-named destination procedure cannot satisfy a missing source body.
- The original strategy and Databricks generation capabilities. These are not
  assertions of semantic equivalence or runtime support.
- Hashed input-file evidence, source diagnostics, unresolved items, assumptions
  and validation requirements.
- Content identity and an input-binding digest to detect stale or inconsistent
  snapshots. These are integrity checks, not digital signatures or semantic
  proofs.

Existing source inference is labelled `UNVERIFIED_LEGACY_ANALYSIS`. File
references identify contributing inputs, rather than claiming exact verified
behavior-to-source mappings. Criticality and behavior weights remain null;
the bridge does not invent business importance or award coverage credit.

The schema lives at
`src/ssis_migration/schemas/migration_contract.v1.json`. Runtime checks also
verify evidence references, complete obligation preservation, input/output
references, strategy, dependencies, content identity and the legacy binding.
Unknown versions, dangling evidence, omitted obligations and changed inputs
fail validation before generation.

## Shared generation boundary

`migration.contract_bridge.build_migration_contract()` builds the existing
dataclass. `validate_contract()` checks a document, optionally against its
planner step. `prepare_generation_step()` validates the binding and supplies
the contract's preserved intent/strategy to legacy consumers without mutating
the caller's intent.

The dispatcher, SQL/PySpark backends, operation builder and Databricks operation
generator check attached contracts. An explicit `UNSUPPORTED` decision cannot
produce implemented operations or call an AI provider. Unsupported behavior
remains an unresolved review artifact in the standard dispatcher path.

AI context → request → prompt carries the same contract. Prompt construction
rejects conflicting request identities, targets and strategies. A model cannot
erase contract validation requirements, source gaps or missing dependencies by
returning `GENERATED`. Supplemental definitions are retained alongside legacy
definitions; differing exported texts remain review findings, without asserting
that textual differences prove semantic differences. Supplemental temporal
requirements remain visible even if a caller supplies a competing override.

The default AI source-context budget is 512,000 serialized characters. An
oversized context fails before the provider call with an explicit diagnostic;
neither the contract nor its source evidence is silently truncated. The budget
can be configured through `AIMigrationPromptBuilder(max_context_chars=...)`.
This application limit is not a promise about a model endpoint's token limit.
Large exports may still require a later dependency-aware context-selection
phase; this bridge does not silently drop definitions to fit them.

## Outcomes and limits

`migration_contract_schema_validation == SUCCEEDED` means structural validation
ran. It does not mean source behavior has been verified. Contract statuses are
`DRAFT`, `REVIEW_REQUIRED` or `BLOCKED`; none means accepted. Contract findings
feed the existing validation results and notebook guards. Execution and data
reconciliation remain unrun, and semantic coverage remains unverified.

This phase does not correct every legacy inference, implement a fully common
IR, add other platform adapters, or implement weighted acceptance. It makes
those future changes reviewable against a preserved contract. No target
workloads are deployed or executed.

## Files and regression checks

| Files | Purpose |
| --- | --- |
| `agent/migration_contract.py`, `migration/contract_bridge.py` | Extend the existing contract and bridge legacy intent without losing information. |
| `schemas/migration_contract.v1.json`, `schemas/__init__.py` | Packaged contract schema and evidence-reference checks. |
| `migrate.py` | Build/save/check all contracts before generation, link artifacts and propagate unresolved findings. |
| `generator/dispatcher.py`, `spark_sql.py`, `pyspark.py`, `operations.py`, `databricks_operations.py` | Enforce attached contracts across deterministic entry points. |
| `agent/context_builder.py`, `planner.py`, `prompt_builder.py`, `orchestrator.py` | Carry the same contract to models and retain obligations/diagnostics. |
| `tests/migration/test_contract_bridge.py`, `tests/test_contract_pipeline.py` | Preserve legacy outputs, reject stale/missing/ambiguous evidence and verify generation ordering and provider boundaries. |

The existing malformed `uv.lock` remains a separate reproducibility issue.
Current-instance tests and wheel validation do not establish frozen-lock
installation or Databricks execution readiness.

Verification for this phase: **317 passed, 4 live Databricks tests skipped**;
`git diff --check` passed. The wheel builds and its packaged contract schema
and binding validator work outside the checkout. The CLI WWI smoke run wrote
13 contracts, each validated against its saved planner step with unchanged
`migration_intent`. The sample remains blocked by source/translation findings;
semantic coverage is unverified and target execution did not run.
