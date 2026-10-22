# Bounded same-cube leaf-rule generation

This increment extends TM1 Databricks candidates beyond constant leaf overlays.
It does not enable general TM1 rule execution or establish source equivalence.

## Supported behavior

The explicit model declaration `STORED_BEFORE_SUPPLIED_LEAF_RULES` enables
N-qualified rules with one complete canonical leaf coordinate per dimension.
Expressions may contain the existing exact-integer arithmetic and literal
`DB('Cube', 'Member1', 'Member2', ...)` reads within that same supplied cube.
Every coordinate must match the dimensional metadata in its declared order.

For example, with stored B=7:

```text
['Product':'A','Measures':'Quantity'] = N: DB('RuleCube','B','Quantity') * 3;
```

evaluates A=21. If B itself has a supplied rule, that rule must be evaluated
first, regardless of where it appears in the file. The capability lowerer
collects explicit cell reads, creates rule-dependency edges and uses a stable
topological order. A read of a cell without a supplied rule uses its explicitly
supplied pre-rule stored value. No missing value or dependency is invented.

Self-references and cycles block the entire candidate, including cycles whose
arithmetic might appear to cancel. Cross-cube/dynamic lookups, unknown members,
partial coordinates, overlapping rules, directives/feeders, STET/CONTINUE,
consolidations, strings, sparse/empty-cell semantics and unsupported functions
remain blocked. All input values, arithmetic intermediates and results must fit
the existing exact IEEE754-safe integer domain. Overflow raises without returning
a partially evaluated snapshot. Failed fixtures prevent runnable artifact generation.

This remains an independent unit snapshot. It does not infer TI→rule→MDX
execution dependencies, transactions, feeder activation or source-engine state.

## Files and compatibility

| File | Responsibility |
| --- | --- |
| `adapters/tm1/translation.py` | Literal DB coordinate/type checks, complete expression lowering, explicit cell reads, cycle detection and dependency ordering. Source statement IDs, order and spans remain in the target program. |
| `schemas/tm1_databricks_mapping.v1.json` | Adds the explicit leaf-rule snapshot declaration. Existing declarations retain their behavior. |
| `generator/tm1/leaf_runtime.py` | Optional portable cell-read evaluator; resolves reads to exact values and reuses existing arithmetic and snapshot validation. |
| `generator/tm1/emitter.py` | Appends the trusted extension only for candidates that use leaf rules; keeps notebooks self-contained and source programs as JSON data. |
| `generator/tm1/pipeline.py` | Reports rule reads, source evidence, dependency edges and evaluation order. |
| `examples/tm1/leaf_rules_demo/` | Self-contained synthetic source/metadata/mapping bundle with two independently specified expected-result cases. |

The existing `runtime.py` is unchanged. Candidates without leaf rules retain the
same utility bytes. The older `STORED_BEFORE_SUPPLIED_CONSTANT_RULES` declaration
does not authorize DB reads. Public legacy calls, provider orchestration, SSIS
IR flow and generic native review-contract guards are unchanged. There is no
new mandatory argument and no implicit promotion to the new profile.

The archived candidate from the preceding commit replayed successfully under
the updated code, retaining all 437 obligations. New target contracts are still
validated by replaying the complete source graph, mappings and capability
lowering. Editing evaluation order, reads, expressions or dependency edges and
recomputing a contract ID does not authorize the altered program.

## Run and validation

```bash
python -m ssis_migration.migrate \
  --source examples/tm1/leaf_rules_demo/manifest.json \
  --target-mapping examples/tm1/leaf_rules_demo/target_mapping.json \
  --output /tmp/tm1-leaf-rules
```

The existing workbench accepts this folder through a source path or as a ZIP
containing all four files. Its metadata, semantic profile, fixture results and
generated artifacts use the existing controls; no source connection is required.

Tests cover stored and evaluated reads, reordered rule dependencies, duplicate
reads, invalid/dynamic/cross-cube coordinates, cycles, overflow, old-profile
compatibility, contract tampering, standalone emitted code, mixed TI/MDX/rule
bundles, notebook/job generation, fixture failure blocking and native acceptance
drafts. Both demo cases are SYNTHETIC. Generation stays GENERATED rather than
ACCEPTED; Databricks execution is NOT_RUN, semantic coverage remains unknown,
and mandatory weighted/critical-behavior/source-observation gates remain intact.

Verification: **1,215 passed, 4 skipped** in the full regression suite. The four
skips require live Databricks provider credentials. This increment adds 25 tests;
the candidate test module now has 107 cases. Installed-only wheel checks passed
all six synthetic cases across the original and new demos, including execution
of the embedded utility code and portable acceptance drafts after original
source folders were removed. The previous-commit candidate still replayed all
437 graph obligations. No live Databricks or source-engine equivalence is claimed.
