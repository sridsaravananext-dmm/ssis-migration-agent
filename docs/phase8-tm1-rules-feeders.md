# Phase 8, second increment: TM1 rules and feeders

## Delivered scope

The previous increment added MDX analysis. This increment adds a separate,
source-specific parser for explicitly supplied plain TM1 rule text, including
ordered calculations and declared feeders. It does not interpret rules as SQL,
MDX or TurboIntegrator, evaluate cell values or generate Databricks workloads.
The complete TM1 package adapter remains unregistered.

## Preserved behavior

| Source construct | Representation and boundary |
| --- | --- |
| SKIPCHECK, FEEDSTRINGS, UNDEFVALS, FEEDERS | Exact ordered directive occurrences; no sparse-cell/default-value behavior is inferred. |
| Area calculations | Selector coordinates, optional declared dimension qualifier, N/C/S qualifier or unqualified source, full expression and original order. |
| Overlapping/repeated areas | Preserved as separate source statements; not deduplicated or reordered. Matching/precedence requires dimensional metadata and runtime review. |
| STET and CONTINUE | Distinct control nodes and review dependencies, never reduced to NULL, zero or a skipped row. |
| DB lookups | Complete argument order and literal/dynamic/empty target expression. No cube name, coordinate binding or execution branch is invented. |
| Feeders | Source-area and target-expression spans linked to their original statement as `DECLARED_FEEDER`, with unresolved endpoints. A feeder is not treated as ETL row movement. |
| Numeric/string syntax | Literal spellings, unary operators, arithmetic/comparisons/logical operators, string operators and grouping. No numeric evaluation or type coercion occurs. `/` and `\` stay distinct. |
| Bounded function signatures | DB, IF, ABS, MIN/MAX, ROUND, NUMBR/STR, ATTRN/ATTRS, ELISANC/ELISCOMP, ELLEV and DIMIX forms. Recognition is syntactic, not proof of correct server semantics. |

Only single-quoted coordinate/string syntax, simple dimension context identifiers
and the documented plain-text subset are accepted structurally. Doubled quotes,
Unicode selectors, scientific numeric spellings and hash comments preserve
source evidence. Include/import-like hash directives are explicitly unsupported,
not discarded as comments. Multi-part hierarchy selectors, dynamic selectors,
unknown functions/signatures, bare unresolved names and additional unsupported
statements remain explicit findings.

Repeated directives, feeders without an explicit FEEDERS section, calculations
or other directives after FEEDERS, and unsupported feeder targets retain their
source structure but receive `UNSUPPORTED` findings. Delimiter/missing-token
errors do not retain a successful parsed prefix. Full original source text/hash
remains available in every returned analysis report.

`STRUCTURED` means the bounded source structure was preserved, not that a TM1
server would accept or evaluate the rules. Operator trees follow this grammar;
actual numeric precision, precedence in the source version, control behavior,
consolidation and error/empty-cell behavior still require source verification.
No TM1 server version is guessed.

## Missing evidence and integrity

The schema-validated `tm1_rules_analysis` report includes complete decoded source,
UTF-8 hash/size, Unicode character spans, ordered statement IDs, source evidence,
references, dependencies, unresolved feeder relations and features. Its content
identity includes the complete report. No source cube is inferred from the
filename, rule areas or the first DB lookup.

Dependencies explicitly cover source-cube metadata, cell types and area binding,
rule ordering/overlap, aggregation and empty cells, security, dimension context,
external cube metadata, DB coordinate order/types, dynamic/empty lookup targets,
attributes and feeder activation/reprocessing. Literal cube arguments are declared
references, not proof the cube exists. All bindings remain unresolved and all
required metadata/context is `NOT_SUPPLIED` in this analysis-only increment.

The public validator checks the offline schema, source/content hashes, evidence
file/span consistency, statement ordering/nonoverlap, unique identities and
exactly one matching relation per declared feeder. A changed source, reordered
statement, omitted feeder or altered endpoint cannot silently keep valid
structural evidence. Hashes detect stale edits; they are not signatures or proof
of true source-review assertions.

Every report retains `semantic_status: NOT_VERIFIED`, no inferred assumptions,
and `migration_accepted: false`. Structural completion does not award weighted
coverage or satisfy critical behavior, execution or data-equivalence gates.

## Offline interface and limits

```bash
.venv/bin/python -m ssis_migration.adapters.tm1.rules \
  --source examples/tm1/rules/sales_rules.rux \
  --format plain-rux-text \
  --output /tmp/new-rules-analysis.json
```

The explicit format declaration does not establish a server/export version.
Binary/proprietary export layouts are not decoded. Source files must be regular
UTF-8/UTF-8 BOM files. The CLI hashes original bytes separately from decoded text,
verifies freshness and creates new output exclusively. Existing source/reports
are not overwritten, and symlinks are rejected. Exit codes are 0 for structural
completion, 1 for unsupported/invalid/limited analysis and 2 for file/argument or
evidence errors. Exit 0 never means migration acceptance.

Programmatic entry point:

```python
report = analyze_rules(
    source_text, export_format='plain_rux_text', file_id=inventory_file_id,
)
validate_rules_analysis(report)
```

Parsing is bounded to 1 MiB of UTF-8 source, 50,000 tokens, recursive expression
limits and AST depth 24. Limits never establish success on a parsed prefix.
No source connection, SQL/MDX fallback, AI request, rule evaluation or workload
execution is performed.

## Compatibility and changed files

A `.rux` extension without a confirmed signature remains an unconfirmed rules
language hint. It does not establish TM1 platform/version or a source cube.
This hint also avoids SQL tokenization for explicitly supplied rules text. Real
rule/TI signatures retain existing detection and blocked package-adapter status.

Recursive rules and MDX AST schemas now select validation branches through an
explicit node tag. This preserves the public schema meaning while avoiding
repeated traversal of unrelated node types for deeply nested source trees.
No provider, existing SSIS parser/IR interface, migration contract, generation
strategy or acceptance gate changed, and no dependency was added.

| File | Responsibility |
| --- | --- |
| `adapters/tm1/rules/lexer.py`, `parser.py` | Separate bounded rule/feeder grammar preserving source order. |
| `adapters/tm1/rules/analysis.py`, `__init__.py` | Structured source evidence, unresolved dependencies and feeder-link integrity. |
| `adapters/tm1/rules/__main__.py` | Explicit-format offline CLI and stable input/output handling. |
| `schemas/tm1_rules_analysis.v1.json`, `schemas/__init__.py` | Packaged versioned rule analysis schema. |
| `schemas/mdx_analysis.v1.json` | Equivalent node-tag validation dispatch for recursive MDX expressions. |
| `intake/inventory.py` | Unconfirmed `.rux` language hint. |
| `tests/adapters/tm1/` | Rule order/qualifiers, dynamic lookups, controls, unsupported syntax, limits, provenance, feeder-link checks, CLI and portable golden summary. |
| `tests/intake/test_tm1_hints.py`, `tests/languages/mdx/test_analysis.py` | Hint regression and nested tagged-node validation checks. |
| `examples/tm1/rules/` | Clearly synthetic example; source metadata deliberately absent. |

Source paths are relative to `src/ssis_migration/`, except tests/examples.

## Verification and next step

Focused rules/MDX/intake/schema suite: **177 passed**. The wheel-only CLI/schema
check passed outside the checkout, preserving six source statements, one
unresolved feeder and 16 missing dependencies in the synthetic example.
Full regression suite: **760 passed, 12 skipped**. Eight skips require the
.NET SDK, which is unavailable in this execution environment; four are existing
live integration tests. No C# compiler behavior was changed by this increment.
The final focused suite passed 177 tests after tagged-schema validation changes.
Offline wheel build and final wheel-only rules/MDX checks passed. Diff checks
passed. These new changes are local on `GenericAITool-version01` and have not
been committed or pushed.


No real TM1 rules were evaluated, no feeder was expanded and no Databricks code
was generated. The next increment is TurboIntegrator section/control-flow
analysis (now implemented in the subsequent
[TurboIntegrator increment](phase8-turbointegrator-analysis.md)), followed by
supplied dimensional metadata binding and the TM1 adapter /
neutral-IR / migration-contract integration. The existing neutral IR's legacy
projection boundary is not bypassed with a fabricated SSIS PackageIR.
