# Phase 7, fourth increment: real C# compilation and observed-case equivalence

## Delivered behavior

The framework can now compile complete C# source files with the actual Roslyn
compiler against explicitly supplied reference assemblies. A separate offline
comparator checks source/target observations against the current migration
contract and candidate. Neither production command executes source assemblies,
candidate functions, Spark jobs or deployments.

Controlled integration tests do execute this repository's synthetic C# fixtures
under .NET and compare their complete state and task results with generated
Python. Six supported-subset scenarios cover **29 input cases**, including
signed int32 overflow, negation, branching, booleans and Unicode strings. Two
additional real-compiler tests reject unresolved symbols and absent SSIS
references. These tests use explicitly identified test-only Dts stubs; they do
not establish actual SSIS runtime or Databricks migration acceptance.

## Full compilation, without fabricated references

`languages/csharp/compiler.py` invokes a selected SDK's `csc.dll` directly.
Callers supply a versioned `csharp_compilation_request` with:

| Field | Meaning |
| --- | --- |
| `request_version` | `1.0`. |
| `scope` | `SUPPLIED_REFERENCES` or explicit `FIXTURE_STUB_RUNTIME`. |
| `sdk_version` | Exact installed SDK version. No implicit SDK substitution. |
| `language_version` | Explicit numeric C# version, such as `12.0`; not inferred from source. |
| `check_overflow` | Explicit compiler overflow option. |
| `output_kind` | `LIBRARY` or `CONSOLE`; console compilation additionally requires `main_type`. Neither kind is executed. |
| `sources` | `.cs` files with absolute paths and SHA-256 hashes. |
| `references` | `.dll` reference files with absolute paths and SHA-256 hashes. |

The compiler uses `/noconfig` and `/nostdlib+`. It does not restore packages,
load user project files, run custom build targets/analyzers, enable unsafe code
or supply missing assemblies. Reference files define the API/framework surface;
.NET 8 references used by fixtures are not defaults for legacy SSIS projects.
An actual SSIS export may require its original .NET Framework reference set,
SSIS assemblies, project sources, resources and compiler settings. Missing
symbols/assemblies cause compiler failure, not a fallback to fixture stubs.
The current standalone request supports source and assembly files; additional
project resources/options remain outside its interface and must be reported as
unresolved rather than treated as a complete project build.

Compilation copies verified inputs into a new output directory, records all
hashes, compiler identity, options, command, log and assembly, and rechecks the
original inputs after compilation. Existing output directories are not reused.
Inputs are limited to 128 source files, 512 references and 32 MiB per file;
compilation has an explicit 1–120 second timeout (60 seconds by default).
Missing tools, changed evidence and timeouts cannot become passing results.

A versioned `csharp_compilation_report` distinguishes `PASS`, `FAIL`, `BLOCKED`
and `TIMEOUT`. `PASS` means full compiler success for the supplied sources,
references and options. Runtime binding and semantics remain `NOT_VERIFIED`;
`source_execution` remains `NOT_RUN`, because this stage does not run output.
The scope and referenced-file hashes expose whether a fixture environment was
used. No claim of real SSIS binding is made from compilation alone.

Example invocation, after supplying a valid request:

```bash
.venv/bin/python -m ssis_migration.languages.csharp.compiler \
  --request /absolute/path/compilation-request.json \
  --dotnet /absolute/path/to/dotnet \
  --output /absolute/path/new-compilation-output
```

Exit codes: 0 for compilation pass, 1 for a non-passing compilation report,
2 for invalid arguments/request or an unusable output path. The SDK is optional
external tooling and is never installed or downloaded automatically by the tool.

## Contract-bound observation comparison

`validator/csharp_equivalence.py` revalidates the existing contract and regenerates
its deterministic candidate. Both observation traces must identify the exact
contract, source SHA-256, generated code SHA-256 and compilation report digest.
The compilation must pass, contain the contract-bound source and use the supplied
unchecked overflow context. Unsupported candidates cannot acquire equivalence
through externally supplied traces.

`csharp_execution_trace` records source/target identity, runtime scope and
nonempty, uniquely named cases. Each case contains the complete input-variable
state and outcome: returned/thrown status, task result, post-state and exception.
The comparator requires identical case sets and input states, strict types,
supplied variable types/permissions, unchanged read-only variables, complete
post-state and the supplied success result. Missing cases, stale bindings,
changed values/types, incomplete outputs, exceptions and conflicting scopes
block or fail comparison. Matching exceptions do not earn successful coverage.
Booleans are not treated as equal to integer 0/1.

The CLI also verifies the request, input snapshots, compiler log and assembly
hashes within the compilation directory. External/changed artifacts are rejected.
The API accepts an optional `compilation_root`; without it, artifact verification
is explicitly false and observations remain unverified operator attestations.
Use that argument when checking an actual saved run. Hash verification detects
changes and omissions, not fabricated observations or authenticated identities.

```bash
.venv/bin/python -m ssis_migration.validator.csharp_equivalence \
  --contract /absolute/path/migration-contract.json \
  --compilation /absolute/path/compiled/compilation_report.json \
  --source-trace /absolute/path/source-trace.json \
  --target-trace /absolute/path/target-trace.json \
  --output /absolute/path/new-equivalence-report.json
```

Exit codes: 0 for an observed-case match, 1 for mismatch/blocked evidence,
2 for invalid inputs or an existing/unwritable output file. No workloads execute.

Reports explicitly retain:

- `scope: OBSERVED_CASES_ONLY`, and the supplied source runtime scope.
- `status: MATCH`, `MISMATCH` or `BLOCKED` with per-case findings.
- Artifact verification status and all observation/candidate binding digests.
- `migration_accepted: false` and `weighted_semantic_coverage: null`.

The supported target observations are `PYTHON_DRIVER`. Local Python execution
does not establish Spark, Databricks job ordering, reconciliation or production
data behavior. Observations of a real source runtime must be supplied/reviewed
separately; this framework does not connect to legacy servers to obtain them.

## Acceptance and compatibility

The existing provider, parser → IR → analysis flow, migration contracts and
weighted acceptance evaluator are preserved. No public signature was removed.
Candidate metadata now explicitly records `source_compilation_status: NOT_RUN`
and requires real source compilation; a synthetic fixture pass does not alter
that status for a real package. No generation strategy or acceptance gate is
promoted merely because a fixture compiled or matched.

An observed-case match is not a general equivalence proof or an 80% semantic
coverage claim. A migration still requires source review, critical-rule checks,
reviewed weights, actual execution and data-equivalence evidence for all required
acceptance gates. Reports can be cited in the existing reviewed-evidence process;
they do not automatically certify unrelated behavior.

## Changed files

| File | Purpose |
| --- | --- |
| `languages/csharp/compiler.py` | Offline Roslyn compilation, artifact verification and CLI. |
| `validator/csharp_equivalence.py` | Contract/current-candidate binding and strict trace comparison. |
| `generator/csharp.py` | Explicit pending source-compilation status/requirement. |
| `schemas/csharp_compilation_request.v1.json`, `csharp_compilation_report.v1.json`, `csharp_execution_trace.v1.json`, `csharp_equivalence_report.v1.json`, `schemas/__init__.py` | Packaged offline schema validation. |
| `tests/languages/csharp/test_compiler.py` | Evidence changes, invalid requests, timeout, safe compiler arguments and output/artifact integrity. |
| `tests/generator/test_csharp_compiled.py` | Actual Roslyn compilation and .NET/Python differential fixtures. |
| `tests/generator/test_csharp_evidence.py` | Stale observations, missing cases, strict types, output/error comparison and non-acceptance. |
| `tests/generator/csharp_fixtures.py`, `test_csharp.py` | Shared existing contract factory without duplicated fixture construction. |
| `tests/fixtures/csharp/` | Explicit test-only Dts runtime and controlled runner. Never fallback project metadata. |

Source paths are relative to `src/ssis_migration/`, except tests/docs.

## Verification

The local toolchain is .NET SDK **8.0.426**, runtime/reference pack **8.0.32**,
installed under `/tmp/ssis-dotnet8` for this verification. Real compiler tests
are enabled explicitly; environments without a .NET 8 SDK skip those tests
with a reason and never claim compilation success.

```bash
SSIS_TEST_DOTNET=/tmp/ssis-dotnet8/dotnet .venv/bin/python -m pytest -q
```

- Full suite with real .NET integration enabled: **654 passed, 4 live integration tests skipped**.
- Focused C#/compiler/comparison/adapter suite: **132 passed**.
- Offline wheel build and wheel-only compiler/comparator CLI checks passed.
- Diff checks passed. At this verification checkpoint, work was local on
  `GenericAITool-version01`.

The compiled comparison fixtures have passed all 29 supplied input cases; full compiler
negative tests reject syntax-valid unresolved symbols and missing SSIS references.
The new compiler and comparator CLIs also pass from the built wheel imported
outside the checkout, with module paths checked to ensure the wheel is used.
A networked isolated build could not reach the package index in this environment;
the wheel was successfully built offline from cached build dependencies.

No user-supplied assembly, real SSIS workload, database, Spark job, live model,
Databricks deployment or destructive workload executed. The only executed C#
was this repository's controlled synthetic fixtures. Real SSIS binding and
Databricks/data-equivalence acceptance remain pending the appropriate exported
references, source project information and reviewed execution/data evidence.


The next independent platform increment is the
[Phase 8 MDX foundation](phase8-mdx-foundation.md). Actual C# runtime/data
acceptance requirements described here remain in force.
