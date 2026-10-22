# Static public subsets in TM1 MDX generation

This increment resolves a bounded `TM1SubsetToSet` expression from supplied
metadata before generating Databricks code. No TM1 connection, subset query,
expression evaluation or security-principal lookup is performed.

```mdx
SELECT TM1SubsetToSet([Product].[Product], 'Selection', 'public') ON 0,
       {[Measures].[Measures].[Quantity]} ON 1
FROM [QueryCube]
```

The metadata must declare a unique public subset in the exact dimension and
hierarchy, with owner null, an explicit nonempty ordered `members` array and
expression null. The hierarchy must be a declared cube coordinate; every member
must be a known canonical numeric leaf. The expansion preserves array order,
including order that differs from the hierarchy's general member declaration.

`Head`, `Tail` and `CrossJoin` can compose these subsets using the existing
bounded set rules. Inputs are fully checked before selection: missing/duplicate
members cannot be hidden behind `Head(..., 1)`. The whole MDX cellset must still
bind every cube dimension explicitly, avoid axis/slicer overlap and fit the
preview bound. Empty subsets/axes, duplicate tuples and implicit defaults remain
unsupported.

Only an explicit public scope is accepted, case-insensitively. Private subsets
are not selected even when they share the public subset's name. Omitted scope,
private owners, dynamic subset expressions/names, key/property hierarchy paths,
missing or ambiguous declarations, unknown members and unsupported expression
forms block the entire candidate. A dynamic subset's expression is preserved as
source evidence, never evaluated or replaced with an assumed static result.

## Implementation and compatibility

| File | Change and purpose |
| --- | --- |
| `adapters/tm1/translation.py` | Reuses the metadata Catalog for scoped subset lookup, canonical member validation and ordered tuple expansion. Adds optional catalog context to existing MDX/set lowering. |
| `generator/tm1/pipeline.py` | Reports each subset expansion, its original source span/file evidence, ordered members and exact metadata declaration pointer. |
| `tests/generator/test_tm1_generation.py` | Semantic boundaries, ordering/composition, private/public collisions, tamper rejection, report/contract replay, ZIP intake, standalone code and acceptance gating. |
| `examples/tm1/static_subset_demo/` | Complete synthetic source, metadata, mapping and two manually specified expected-result cases. |

The target contract's new optional `subset_bindings` field is present only when
a subset is used. It binds each expansion back to the original MDX call and
metadata declaration. Contract validation reparses/rebuilds the complete source
and expansion; changing members, pointers, spans, requests or removing bindings
and recomputing a contract ID is rejected.

Target execution reuses the existing `cell_query` program and runtime. Existing
source mappings, literal MDX programs, constant/leaf-rule kernels and notebook
utility bytes remain unchanged. No new CLI argument, mandatory metadata field,
provider change or SSIS interface change is introduced. Archived candidates from
the previous increment replayed successfully with 437 and 98 preserved obligations.

## Run

```bash
python -m ssis_migration.migrate \
  --source examples/tm1/static_subset_demo/manifest.json \
  --target-mapping examples/tm1/static_subset_demo/target_mapping.json \
  --output /tmp/tm1-static-subset
```

In the existing workbench, use this source folder/path or upload a ZIP containing
all four example files. Select its target mapping, configure the existing target
settings and generate/download artifacts. No frontend workflow change is needed.

Both examples are SYNTHETIC and do not claim source-engine execution. Local
fixtures, notebook structure, source/contract/artifact replay and ZIP intake are
checked independently. Acceptance drafts retain every graph obligation and leave
weights, criticality and mandatory gates unassessed. Databricks execution remains
NOT_RUN; semantic/source-equivalence coverage remains unverified.

Verification: **1,244 passed, 4 skipped** in the full regression suite, including
29 new subset cases (136 candidate-generation cases in total). The four skips
require live Databricks provider credentials. An installed-only wheel passed all
eight synthetic cases across the original, leaf-rule and subset demos, including
execution of emitted utility code and acceptance drafts after original source
folders were removed. Older candidate replays preserved all 437 and 98 obligations.
