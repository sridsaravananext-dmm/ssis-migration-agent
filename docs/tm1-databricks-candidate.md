# TM1 semantic capability checks and Databricks candidate generation

Databricks human review is deferred at the user's request; offline implementation
continues without that checkpoint. Execution and production equivalence remain
unverified. Existing SSIS generation and native TM1 review contracts are preserved.
See [offline evidence validation](offline-evidence.md) and the
[XML adapter profiles](xml-source-adapters.md) for subsequent implementation.

## Files and interface changes

| Files | Change and purpose |
| --- | --- |
| `adapters/tm1/translation.py` | Source-specific capability checks and complete-unit lowering; keeps vendor semantics out of common generation. |
| `generator/tm1/contract.py` | Replayable, versioned target contract with preserved source/mapping evidence and all graph obligations. |
| `generator/tm1/runtime.py` | Pure target numeric cell kernel; no IO, source parsing or source command evaluation. |
| `generator/tm1/emitter.py` | Self-contained PySpark notebooks, explicit expected-result reconciliation and validated test-job templates. |
| `generator/tm1/pipeline.py` | Contract-before-code orchestration, input freshness checks, reports, SHA-256 inventory and ZIP handoff. |
| `schemas/tm1_databricks_{mapping,contract}.v1.json`, schema registry | Strict offline input and contract validation, included in the wheel. |
| `migrate.py` | Backward-compatible optional `target_mapping` API argument and `--target-mapping` CLI flag. |
| `examples/tm1/databricks_demo/` | Separate synthetic TI/MDX/rule source bundle, dimensional metadata and four manually specified fixture cases. |
| `tests/generator/test_tm1_generation.py` | Capability rejection, replay/tampering, integer boundaries, ordering, skip behavior, notebook/job integrity and ZIP verification. |

No dependency, provider, common generation contract or migration_intent interface
was replaced. Other platform adapters remain blocked.

## Verification results

Full regression: **1,034 passed, 12 skipped**. Focused candidate suite:
**72 passed**, including the reserved `all` unit-ID guard. Eight skips require
the unavailable .NET 8 SDK; four require the existing live Databricks integration.
These skips do not verify C# compilation, equivalence or live Databricks execution.

The final wheel built offline and passed installed-only checks outside the
checkout: three native candidate programs, four manually specified synthetic
fixture cases, all **437** source graph obligations, contract/schema replay,
archive SHA-256 verification and SSIS envelope/IR v1 compatibility. The final
handoff ZIP also reproduced both notebooks, target job graph and utility source
under the current capability checks. Tracked/new-file whitespace checks passed.

Databricks/PySpark execution, independent TM1 observations, business criticality,
weighted semantic coverage and data equivalence remain pending. This checkpoint
does not claim general TM1 migration or acceptance. The working branch is
`GenericAITool-version01`; Databricks review is optional for continuing coding.

## Run

From an installed checkout:

```bash
python -m ssis_migration.migrate \
  --source examples/tm1/databricks_demo/manifest.json \
  --output generated_output \
  --source-platform tm1 \
  --target-mapping examples/tm1/databricks_demo/target_mapping.json
```

The API adds one optional keyword, `target_mapping`, to `run_migration`. Omitting
it preserves the existing review-only native route. SSIS refuses the new argument
instead of ignoring it. Source execution and AI generation remain unavailable for
native TM1. No provider interface, migration_intent, SSIS IR or parsing order changes.

Outputs occupy a unique directory and adjacent ZIP. `DATABRICKS_REVIEW.md` inside
contains the manual workspace import, fixture run and optional read-only table
preview instructions. No Databricks authentication, deployment, persistent writes,
source process execution or migration acceptance occurs during generation.

## Supported scope

An explicit, versioned `tm1_databricks_mapping_v1` document binds its SHA-256 to the
supplied source bundle. It supplies physical column mappings, target input tables
(or null for fixtures), source-record ordering, all process parameters, semantic
declarations and independent expected-result cases. Declarations are preserved
as unverified evidence, never invented or treated as source observations.

The sole semantic profile is `dense_numeric_leaf_snapshot_v1`: complete numeric
leaf-only coordinate domains, exact canonical identifiers and integer inputs and
intermediate results within ±(2^53−1). No null, fractional, sparse, string-valued,
consolidated, hidden-rule/feeder or security-filtered semantics are claimed. The
preview reads at most `max_rows+1` to detect oversize and refuses processing rather
than silently truncating. Complete cell domains must also fit max_rows (≤100,000).
It is a bounded driver-side semantic kernel wrapped in PySpark, not a distributed
production migration engine.

| Source behavior | Candidate strategy | Rejected examples |
| --- | --- | --- |
| TI Data section row assignments, numeric +/−/* and comparisons, IF/ELSE, ItemSkip, CellPutN | Explicit ordinal sorting; separate per-record locals; last write to a coordinate wins; earlier writes survive ItemSkip | Nonempty prolog/metadata/epilog, loops, functions, source queries, commands, processes, persistent variables, implicit defaults, increments, destructive calls |
| Literal MDX member/tuple sets, bounded static CrossJoin/Head/Tail and slicer with every cube dimension explicit | Read explicit leaf cells, retain axis positions and axis-0-fastest cell order | NON EMPTY, general functions, private/dynamic subsets, defaults, properties, aliases, aggregation, subqueries, duplicate tuples |
| TM1SubsetToSet with explicit public scope and static supplied metadata | Expand canonical ordered members with source/metadata evidence, then reuse the existing bounded cell-query kernel | Private/dynamic subsets, omitted scope, ambiguous declarations, empty/duplicate/unknown members and unsupported hierarchy paths |
| Full-coordinate N-qualified numeric constant rules | Constant overlays on explicitly pre-rule stored cells | Feeders, directives, overlapping rules, dynamic/reference expressions, string rules, ambiguous source file precedence |
| Explicit same-cube leaf-rule DB reads | New pre-rule leaf snapshot declaration; dependency order derived from literal reads, including evaluated rule cells | Cycles, dynamic/cross-cube reads, noncanonical members, unsupported arithmetic/functions and sparse/consolidated semantics |

A source unit is translated as a whole. An unsupported construct, conflicting
metadata, unused/missing mapping or failed fixture blocks executable output for
the entire package. Cross-unit rule evaluation is blocked. Units run independent
snapshots; no source execution dependencies are guessed. Input/output snapshots
represent final candidate cell states, not source transactions, locking, error
behavior or TM1 engine lifecycle equivalence.

See [same-cube leaf-rule generation](tm1-leaf-rule-generation.md) for the new
opt-in semantic declaration, dependency/evidence reporting and standalone demo.
The earlier constant-rule profile remains unchanged and still rejects DB reads.
Static public subsets are described in [their capability guide](tm1-static-subset-generation.md);
the literal-query and existing rule runtimes are unchanged.

The separate synthetic demo supplies LoadCube, QueryCube and RuleCube and full
metadata. Original incomplete examples keep their original missing-evidence and
unsupported-behavior blockers. The demo was not derived from an actual TM1 engine.

## Contract and validation boundary

The source-specific lowerer lives in `adapters/tm1/translation.py`. The target
contract is generated before code and contains every native graph obligation,
all preserved source evidence, mapping bytes/hash, capability decisions, portable
programs, assumptions and mandatory review requirements. Validation replays the
native parsers/IR and capability lowering. Rehashing an edited program, removing
an obligation or changing an embedded mapping cannot authorize altered behavior.

The generator reuses NotebookAssembler cell factories/static validation and the
existing JobGenerator graph validation. Native source REVIEW_ONLY contracts and
the generic contract bridge's guard remain intact. The new contract, runtime,
emitter and pipeline live under `generator/tm1/`; schemas ship in the wheel.
Generation never calls a model, evaluates source text or dispatches arbitrary
source calls. The standalone notebooks embed the exact reviewed utility source,
portable programs and fixtures as JSON data; no checkout installation is needed.

`Migration.ipynb` defaults to fixtures and displays temporary views.
`Validation.ipynb` reconciles explicit expected and actual Spark DataFrames in
both directions with `exceptAll` and row counts. `job.json` and `databricks.yml`
are optional templates requiring a user-selected existing cluster and workspace
notebook root. No cloud/hardware defaults, schedule or deployment occurs. The
validation task depends on fixture previews as a target testing workflow only.

A source review report is retained separately. The generation report distinguishes
source structure accounting, local target-kernel fixtures, static validation,
Databricks execution and source/data equivalence. All synthetic fixtures are
labeled accordingly. Semantic weights/criticality remain unknown, execution is
NOT_RUN and acceptance is false even when candidate generation succeeds.

## Evidence and continued implementation

Import and run the generated validation notebook in your Databricks environment,
inspect preview outputs and save runtime/input/output evidence. Actual source
observations and business criticality/weights are still required. At least 80%
weighted verified semantic coverage and every critical gate must pass before
acceptance. Native review-only outputs remain ineligible for acceptance. A
separate replayable candidate loader now prepares unassessed acceptance drafts;
synthetic fixtures earn no source-equivalence credit.

Further adapter implementation does not depend on a human Databricks review.
Bounded static CrossJoin, Head and Tail over explicit literal MDX leaf sets are
now supported. CrossJoin preserves operand order and requires disjoint dimensions;
Head/Tail require positive bounded literal counts. Every input set is validated
before selection, so pruning cannot hide missing members or duplicate tuples.
Consolidations, empty/sparse cells, general MDX/rules/TI semantics and production
acceptance remain outside this bounded candidate.

The later offline continuation installed .NET 8.0.426 and passed all eight real
C# fixture compilation/comparison tests. Actual SSIS runtime binding and general
source-to-target migration equivalence remain unverified.
