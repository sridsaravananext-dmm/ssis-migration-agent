# Phase 7, second increment: offline SSIS C# assessment

The previous increment bound neutral graph behavior to plans and contracts.
This increment adds conservative inspection of explicitly exported C# source.
The existing SSIS parser, legacy IR, planner, provider interface and generators
remain unchanged. No new dependency is installed.

## Behavior and evidence

The SSIS adapter recognizes native ScriptTask and ScriptComponent types through
an explicit allowlist. Exact native references associate XML objects with IR
records; ambiguous/missing references do not select a source arbitrarily.
Names and heuristic component labels do not establish script identity.

Supported source exports are text-only `File` or `ProjectItem` elements with
an explicit `.cs` name and absent/UTF8/UTF-8 encoding. The adapter never follows
the filename, loads an assembly, decompresses binary payloads or executes code.
Nested task/component sources cannot be attributed to a parent script object.
Binary-only, other-language and alternate layouts remain explicitly unresolved.
`source_status: AVAILABLE` means at least one supported source entry was found,
not that the complete project and all its dependencies were supplied.

Each source assessment preserves decoded source text, its UTF-8 SHA-256,
inventory file identity, XML child-index location, finding offsets and line
numbers. The source digest covers decoded XML text; the existing intake digest
covers the original package bytes. XML locators start at the package root and
follow zero-based child positions. They do not imply compilation or semantic
verification. Entire package hashes continue to be checked after analysis.

The common language layer masks ordinary/verbatim strings, characters and
comments before identifying lexical signals for SSIS variables/events/results,
connections, file I/O, processes, database clients, network access, dynamic or
native code and transaction boundaries. A packaged offline JSON Schema checks
every returned assessment. Raw/interpolated strings, preprocessor directives
and unterminated literals/comments force review.

| Classification | Meaning in this increment |
| --- | --- |
| `PARTIALLY_TRANSLATABLE` | Recognized SSIS interaction patterns are candidates for later translation; complete behavior remains unknown. |
| `REQUIRES_REDESIGN` | Lexical signals identify effects or runtime dependencies needing an explicit Databricks design and review. |
| `REVIEW_REQUIRED` | Source/identity is missing, the syntax needs deeper analysis, or no supported signal was found. |

These rules cannot establish a `TRANSLATABLE` result. Symbol resolution, aliases,
custom method bodies, assembly references and complete C# syntax/control flow
require a future syntax-aware stage. A name can be shadowed or used in a custom
API; lexical findings are review signals, not authoritative API binding or
lineage. No percentage of translated behavior is inferred from the findings.

## Integration and compatibility

Assessments are attached to neutral task/component node attributes. An explicit
`UNVERIFIED_CSHARP_SCRIPT` diagnostic remains alongside existing unsupported
semantics. The task/component operation stays `UNKNOWN`; triage does not promote
a legacy generation strategy. Graph identity includes the source assessment.

The existing neutral-plan bridge preserves the entire enriched node as a
contract obligation, including exact source, findings and unresolved items.
Contract hashes and package evidence inventories therefore change when source
changes. Existing acceptance gates still require reviewed weights/criticality,
semantic evidence, controlled execution and data reconciliation. No script is
automatically accepted or executed. Standalone legacy parsing and normalization
without the optional `script_assessments` argument keep previous behavior.

## Changed files and checks

| File | Responsibility |
| --- | --- |
| `languages/csharp/assessment.py`, `__init__.py` | Offline lexical assessment with retained source evidence. |
| `adapters/ssis/scripts.py` | SSIS-specific script identity and explicit source extraction. |
| `adapters/ssis/adapter.py`, `normalizer.py` | Attach assessment to neutral records without changing legacy IR. |
| `schemas/csharp_assessment.v1.json`, `schemas/__init__.py` | Validate assessment documents offline. |
| `tests/languages/csharp/test_assessment.py` | Lexical signals, masking, uncertainty, offsets, hashes and schema rejection. |
| `tests/adapters/test_ssis_scripts.py` | Exports, encodings, nested isolation, ambiguity and contract preservation. |

Source paths are relative to `src/ssis_migration/`, except tests.

Focused suite: 105 passed (language, adapters, neutral binding and IR tests).
Full suite: **550 passed, 4 live integration tests skipped**. The final C# and
adapter tests also passed after checking that every source locator resolves to
its original XML element (28 passed). A rebuilt wheel imported outside the
checkout passed source extraction, locator, classification and offline-schema
checks. Diff checks passed. No source script or target workload was executed.
At this verification checkpoint, changes were local on
`GenericAITool-version01`.

Next work includes syntax-aware C# analysis/translation for explicitly bounded
constructs, then the separate TM1 language/adapter phase. Full PL/SQL semantics
and the remaining source adapters are still pending.


The subsequent [grammar and bounded translation increment](phase7-csharp-syntax-translation.md)
adds complete-file C# grammar parsing and an explicit contract-bound candidate
path. The lexical-assessment limits described above remain relevant; full .NET
compilation and unrestricted C# translation are not claimed.
