# Phase 7, third increment: C# grammar parsing and bounded translation

## Result and scope

Exported C# assessments now include complete-file grammar analysis, including
recovery/missing-token diagnostics. A new opt-in, contract-bound generator can
produce a driver-side Python function for the explicitly bounded
`SSIS_PURE_MAIN_V1` capability. Existing SSIS steps retain their strategies;
script tasks are not automatically promoted to deterministic migration.

This is a supported subset translator with full-file grammar parsing, not a
complete C# compiler or a general ScriptTask/ScriptComponent translator.
Grammar success does not establish valid symbol binding, .NET compilation or
semantic equivalence. The current environment has no .NET compiler/runtime;
source compilation and real SSIS/Databricks execution remain required checks.

## Parsing and provenance

`languages/csharp/parser.py` uses the Tree-sitter C# grammar. It consumes the
whole decoded source, reports errors anywhere in the file, and retains a source
SHA-256, UTF-8 byte count, parser/grammar versions, construct counts, original
file identity and locator. Diagnostics use UTF-8 byte offsets and one-based
line numbers. Existing lexical finding offsets remain Python character offsets;
these are separate fields and must not be interchanged.

Sources over 1 MiB and trees over 100,000 visited nodes are explicitly limited;
no accepted prefix is returned. The language layer never loads source assemblies
or executes C#. Its packaged offline schema checks every syntax report.
Assessments retain lexical findings and add `syntax_analysis`; parse failure
forces `REVIEW_REQUIRED`. Old assessment documents without that optional field
remain readable. Source hashes and graph/contract identities include the new
analysis when present, so changed artifacts require renewed evidence review.

Dependencies added to `pyproject.toml`:

- `tree-sitter>=0.25,<0.26` (tested 0.25.2).
- `tree-sitter-c-sharp>=0.23.1,<0.24` (tested 0.23.5).

## Explicit translation contract

The existing `MigrationContract` remains the generation boundary. An opt-in
step supplies `metadata.csharp_translation` before its contract is built. The
version-1 packaged `csharp_translation_spec` schema defines:

| Field | Required supplied information |
| --- | --- |
| `source_node_id` | Exact, uniquely owned neutral root task node. |
| `source_locator`, `source_sha256` | Exact source export within the contract-bound node. |
| `entry_point` | Fully qualified class/method name, ending in `Main`. |
| `runtime.kind` | Explicit `SSIS_SCRIPT_TASK` runtime binding. |
| `runtime.overflow_context` | Explicit `UNCHECKED` compilation context. |
| `runtime.success_value` | Signed int32 success value from supplied runtime evidence; no default is inferred. |
| `runtime.evidence` | At least one reference to available contract evidence supporting those runtime bindings. |
| `variables` | Exact source names mapped to `int32`, `bool` or `string`, and `READ`, `WRITE` or `READ_WRITE`, each with evidence. |

Evidence references use the existing `{file_id, locator, rule}` format. Supply
actual metadata/export evidence; referencing a file is not proof that its
contents support a binding. Binding correctness and compilation options still
require source review and independent verification. No schema/type/access mode
is inferred from names, casts or AI output.

Conceptual API integration, using an already bound step and supplied metadata:

```python
step['metadata']['csharp_translation'] = supplied_spec
contract = build_migration_contract(
    step, source_platform='ssis', evidence_files=supplied_file_inventory,
).to_dict()
# Dispatcher validates the contract and routes only the explicit opt-in path.
step['metadata']['migration_contract'] = contract
artifact = GeneratorDispatcher().generate(step)
```

Use the exact bound step when rebuilding a contract. Do not modify a persisted
contract's source, types or selectors without rebuilding its hashes. An explicit
`DETERMINISTIC` strategy is required; `UNSUPPORTED`, `AI_ASSISTED` and review
strategies remain unchanged and receive an unresolved result on this path.
Normal steps without `csharp_translation` retain existing dispatch behavior.

## Capability rules

The SSIS adapter owns lowering of SSIS-specific `Dts` behavior into a typed
program. Databricks-specific generation is separate and accepts only that
program after contract/source checks. The generator reparses the bound source;
it never trusts a saved AST or assessment as executable code.

| Supported in this increment | Boundary |
| --- | --- |
| One complete class and `public void Main()` | Optional ordinary namespace; fully qualified known SSIS ScriptTask base; no partial class, fields, constructors, other methods, nested types or attributes. |
| Explicit typed, initialized locals | `int`, `bool`, `string`; one local per declaration, block scopes, no shadowing or unknown identifiers. |
| SSIS variable reads | Literal keys, exact casts matching supplied type and read permission. |
| SSIS variable writes | Literal keys, exact value type and write permission. |
| Literal values | Decimal signed int32, booleans, ordinary strings without escapes. |
| Integer operators | `+`, `-`, `*`, unary signs and integer comparisons. Runtime arithmetic wraps to signed int32 under the supplied unchecked context. Constant overflow is rejected. |
| Boolean logic | `!`, `&&`, `||`, preserving short-circuit evaluation and strict boolean operands. |
| Equality | Same-type int32, boolean or non-null scalar-string equality/inequality. |
| Branches | Explicit `if`/`else` blocks with boolean conditions. |
| Completion | Exactly one final assignment of the supplied literal success value to `Dts.TaskResult`. |

Unsupported expressions, calls, I/O, database/network clients, loops, exception
handling, transactions, preprocessor directives, casts/conversions outside the
subset, dynamic keys, string escapes/interpolation and additional project
behavior prevent any candidate code emission. ScriptComponent row/buffer
semantics and multi-file composition are explicitly outside this capability.
Full ScriptTask source exports are still required for wider translation.

## Candidate behavior and acceptance

The generator defines `run_ssis_script(variables)` and does not invoke it. The
caller supplies an existing state dictionary. Runtime guards reject missing
variables, type mismatches, Python booleans masquerading as integers, values
outside signed int32, null strings and surrogate-containing strings. No defaults
or silent type coercions are introduced. The function mutates explicitly bound
variables in source order and returns the supplied success code.

A separate reviewed integration must populate source state, persist outputs,
and connect result/failure handling to Databricks job control flow. Merely
placing this function into a notebook does not execute or implement a task.
Other graph/contract obligations remain unresolved. Candidate status is always
`REVIEW_REQUIRED`, semantic status `NOT_VERIFIED`, with real execution/data
checks `NOT_RUN`. The generated code also contains the existing review marker,
so notebook/static readiness does not silently grant acceptance.

Missing source, ambiguous/shared ownership, stale selectors/hashes, unavailable
binding evidence, wrong types/permissions, unsupported strategies or unsupported
source return `UNRESOLVED` with no code and an explanation/source span. Invalid
or stale migration contracts are rejected before generation. Target Python is
independently parsed/compiled and checked by the existing code validator.

`examples/csharp/PureMain.cs` is a synthetic demonstration only. Its types and
success value are not defaults for real packages.

## Changed files

| File | Purpose |
| --- | --- |
| `languages/csharp/parser.py`, `assessment.py`, `__init__.py` | Complete-file grammar analysis and assessment integration. |
| `adapters/ssis/csharp_lowering.py` | SSIS-specific, typed, fail-closed subset lowering. |
| `generator/csharp.py`, `dispatcher.py` | Contract-bound candidate generation and explicit opt-in routing. |
| `schemas/csharp_syntax.v1.json`, `csharp_translation_spec.v1.json`, `csharp_assessment.v1.json`, `schemas/__init__.py` | Offline validation and additive schema registration. |
| `pyproject.toml` | Declared parser dependencies. |
| `tests/languages/csharp/test_parser.py`, `tests/generator/test_csharp.py` | Complete parsing, rejection, contract binding, scope, types and controlled semantic fixtures. |
| `examples/csharp/` | Synthetic source and input-contract explanation. |

Source paths are relative to `src/ssis_migration/`, except tests/examples/config.

## Verification

- Full regression suite: **608 passed, 4 live integration tests skipped**.
- Final focused language/generator/adapter/contract suite: **135 passed** after
  the final SSIS adapter module placement. An earlier broader focused suite
  including notebook generation passed 146 tests.
- Built wheel imported outside the checkout and passed complete parsing,
  packaged-schema validation, contract binding, adapter lowering and dispatcher
  candidate generation using the synthetic example. The candidate was not
  invoked during that wheel check.
- Diff checks passed. At this verification checkpoint, changes were local on
  `GenericAITool-version01`; no deployment was performed.

Controlled tests execute only generated pure fixture functions with explicit
input/expected-output cases. They cover int32 overflow boundaries, branches,
boolean/string behavior, variable access and invalid inputs. They do not run a
source assembly, Spark workload, live model, database or deployment, and are not
source-to-target execution evidence for a real migration.

Future C# work includes actual compiler/symbol validation, broader constructs,
project composition and verified runtime/job integration. The next independent
platform phase remains TM1 with separate MDX, rules and TurboIntegrator analysis.


The subsequent [compilation and observed-case equivalence increment](phase7-csharp-compilation-equivalence.md)
adds real Roslyn compilation with explicit references and controlled compiled-C#
versus generated-Python fixture tests. Actual SSIS runtime and Databricks/data
acceptance remain separate requirements.
