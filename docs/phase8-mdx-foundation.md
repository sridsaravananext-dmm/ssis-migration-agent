# Phase 8, first increment: separate MDX analysis

## Why this increment

The existing SQL boundary deliberately rejected MDX, and intake offered only a
language hint. TM1 rules/feeders, TurboIntegrator and MDX are distinct languages;
none should be routed through T-SQL/PL-SQL translation. This first increment
adds a bounded MDX SELECT grammar, dimensional dependency reporting and an
offline CLI. It does not register a complete TM1 package migration adapter or
produce Databricks code from unresolved cube behavior.

The C# compilation/observation tools delivered in Phase 7 remain available.
Real C#/SSIS acceptance still depends on the original exported project references
and reviewed runtime/data evidence; a synthetic fixture match is not promoted.

## Supported structure

The selected dialect is explicitly `tm1`. It is a caller declaration, not proof
that a file came from a particular TM1 server/version. Source platform and export
version are not invented from an `.mdx` filename.

| Area | Preserved structure |
| --- | --- |
| Query | One complete SELECT, optional terminal semicolon, FROM cube and optional WHERE slicer. |
| Axes | Named axes COLUMNS/ROWS/PAGES/CHAPTERS/SECTIONS or ordinals 0–4; NON EMPTY retained; duplicate ordinals rejected. |
| Coordinates | Bracketed identifier segments, member keys, tuple coordinates, member properties and raw source spans. |
| Sets | Empty/nonempty sets, grouping, colon ranges, explicit union/difference/cross-product operators and precedence. Sets are not flattened or enumerated. |
| Member properties | Members, Children, CurrentMember, Parent and DefaultMember are syntax categories; their bindings remain unresolved. |
| Function signatures | CrossJoin, two-argument Union/Intersect/Except, Hierarchize, Head/Tail, TM1FilterByLevel and bounded TM1SubsetToSet forms. Recognition does not establish function equivalence. |
| Quoting/comments | Bracket `]]` escaping, doubled quote escaping, ordinary quoted strings, line/block comments and Unicode identifiers. |

The grammar consumes the whole input. Additional statements, unsupported clauses,
WITH calculations, subselects, named-set references, scalar arithmetic, property
calls and unknown function signatures remain explicit unsupported findings.
Malformed delimiters/missing required tokens have separate diagnostics. There
is no SQL parser or AI fallback, query execution, implicit member/set coercion,
subset expansion, guessed hierarchy or generated relational rewrite.

A `STRUCTURED` result means only that this supported syntactic structure was
preserved. It does not establish valid dimensionality, correct member binding,
server compatibility or semantic equivalence. Axis/slicer evaluation depends on
cube metadata and source runtime behavior. In particular, WHERE slicers are not
assumed to be row filters and NON EMPTY is not reduced to SQL NULL filtering.

## Evidence and missing dependencies

The versioned `mdx_analysis` document preserves:

- Complete decoded source text, UTF-8 SHA-256/byte count and caller file identity.
- Source spans measured in Unicode code points, with per-reference/diagnostic
  evidence locations and explicit rule names.
- A typed query AST, member/cube references and feature inventory.
- Missing cube metadata, member/hierarchy bindings, rules/aggregation,
  security/default context and named-subset definitions.
- Explicit declared subset scope when present; no default public/private scope.
- `NOT_VERIFIED` semantics, no inferred assumptions and `migration_accepted: false`.

All references remain `UNRESOLVED`. Identifier path segments are lexical source
segments, not assertions that a given segment is a dimension, hierarchy, level
or leaf member. Calculations, rule/feeders effects, default members, sparse-cell
behavior, aggregate ordering and cell security require supplied evidence.

Content identity (`analysis_id`) covers the complete report. The public validator
checks its offline schema, source hash/size, content identity, span bounds,
evidence file/locator consistency and duplicate evidence IDs. Hashes detect
stale edits; they are not signatures or proof of true metadata assertions.

Limits are explicit: 1 MiB of UTF-8 source, 50,000 lexical tokens, bounded parser
recursion and AST depth 24. A limit prevents structural acceptance of any prefix;
the report still preserves the supplied text/hash. Unsupported complete trees
can be retained with findings; incomplete parse prefixes are not retained as
accepted queries or references.

## Intake compatibility

TM1 signature hints now ignore comments and quoted strings/identifiers, so text
such as `-- CellPutN(...)` cannot by itself confirm a TI export. An explicit
`.mdx` extension is handled as an unconfirmed language hint before TI hints, so
TI-looking text inside an MDX member name cannot select the TI format.

The existing unsupported-language diagnostic is retained with wording that
acknowledges the bounded analyzer. MDX still has unknown detected platform until
confirmed through existing intake interfaces. Real TI/rule signatures remain
recognized but blocked because their package adapter is not implemented.
Existing SSIS detection, PackageIR → analysis ordering, contracts, generators,
providers and acceptance gates are unchanged.

## Offline interface

```bash
.venv/bin/python -m ssis_migration.languages.mdx \
  --source examples/tm1/mdx/sales_axes.mdx \
  --dialect tm1 \
  --output /tmp/new-mdx-analysis.json
```

The CLI inventories and hashes a regular source file, rejects symlinks, reads
UTF-8/UTF-8 BOM, verifies freshness before writing, and creates a new report
exclusively. Original byte evidence and decoded-source evidence are separate
when a BOM is present. Existing source/report files are never overwritten.
Exit 0 means `STRUCTURED`, exit 1 means a report with unsupported/invalid/limited
source, and exit 2 means an argument/file/evidence error. Exit 0 is not migration
acceptance. No server connection, query or workload execution occurs.

The programmatic interface is:

```python
report = analyze_mdx(source_text, dialect='tm1', file_id=inventory_file_id)
validate_mdx_analysis(report)
```

No new dependency was added. SQLGlot is not used to parse or tokenize MDX.

## Changed files and acceptance tests

| File | Responsibility and checks |
| --- | --- |
| `languages/mdx/lexer.py`, `parser.py` | Separate bounded grammar; complete consumption, delimiters, escaping, precedence and explicit unsupported constructs. |
| `languages/mdx/analysis.py`, `__init__.py` | Typed AST, unresolved references/dependencies, provenance and content validation. |
| `languages/mdx/__main__.py` | Offline, fresh-file evidence and exclusive output CLI. |
| `adapters/tm1/detection.py`, `__init__.py` | TM1 signature helpers only, without registering a package adapter. |
| `intake/inventory.py` | Conservative signature hints and MDX diagnostic wording. |
| `schemas/mdx_analysis.v1.json`, `schemas/__init__.py` | Packaged recursive AST/report schema. |
| `tests/languages/mdx/` | Structural and evidence tests, unsupported/invalid/limited inputs, no SQL fallback, CLI freshness/overwrite tests and a portable golden summary. |
| `tests/intake/test_tm1_hints.py` | Ignore literal/comment signatures, preserve real hints and keep MDX platform unconfirmed. |
| `examples/tm1/mdx/` | Clearly synthetic example and usage note; metadata deliberately absent. |

Source paths are relative to `src/ssis_migration/`, except tests/examples.

## Verification and remaining work

Focused language/intake suite: **89 passed**. The wheel-only CLI check passes
with packaged schema validation and content/evidence checks outside the checkout.
The synthetic example preserves two axes and its tuple slicer while reporting
eight absent metadata/context dependencies; acceptance remains false.

Full suite with the existing .NET integration enabled: **715 passed, 4 live
integration tests skipped**. Offline wheel build and wheel-only CLI/schema
verification passed. Diff checks passed.

Last updated date - 10/22/2026. This release includes updated MDX analysis on
`GenericAITool-version01`.
 No MDX query,
source process, live AI call, database or Databricks workload executed for this
increment. The full suite also retains the existing controlled C# fixture tests.

Next TM1 increments are separate rule/feeder analysis, TurboIntegrator sections
and control flow, supplied multidimensional metadata binding, then the TM1
adapter/neutral-IR/planning integration. The current neutral graph still has a
legacy-IR projection boundary; this change does not fabricate an SSIS PackageIR
for multidimensional source or mislabel an MDX report as a migrated package.
Databricks generation follows only after the relevant source behavior and
migration contract have been established and checked.


The subsequent [TM1 rules and feeder increment](phase8-tm1-rules-feeders.md)
adds ordered plain-text rule analysis and unresolved feeder/dependency reporting.
The MDX and rules analyzers remain separate; package migration is not enabled.
