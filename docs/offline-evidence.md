# Offline validation and source/target observations

The Databricks human-review checkpoint is no longer a prerequisite to coding.
This changes implementation scheduling, not acceptance criteria. The tool does
not connect to source systems or Databricks and does not run arbitrary exports.

## Candidate integrity and acceptance drafts

The existing acceptance command now recognizes a separately validated TM1
Databricks generation contract. Generic native review-only contracts remain
blocked. The loader replays source IR, native review contracts, capability
lowering, target contract, fixture results, notebook/code/job generation and all
inventory hashes. Archived exact source bytes make candidate verification
portable; changed/missing/extra files, symlinks or altered code are refused.

```bash
python -m ssis_migration.validator.acceptance \
  --package-dir /path/to/tm1-candidate \
  --prepare /tmp/new-acceptance-drafts
```

Every neutral graph obligation appears once in the draft policy. Weights and
criticality remain null, checks remain absent and gates remain NOT_RUN.
Synthetic fixtures earn no semantic/execution/data-equivalence coverage.
The existing SSIS acceptance interface remains compatible.

Native candidate acceptance requires `tm1_semantic_profile` and
`independent_source_observations` in addition to existing mandatory gates; editing
the policy cannot remove these package requirements. The source-observation gate
must replay matching original SOURCE_ENGINE/TARGET_ENGINE capture pairs for
every source unit, bound to the current contract, source manifest and complete
artifact hashes. Merely labeling a text report PASS cannot satisfy this gate.
All behavior weights and critical cases must still be assessed separately.
Any resulting acceptance applies only to the bounded preview contract's scope.

## Exact typed reconciliation

Supply two documents conforming to `schemas/data_observation.v1.json`. Each
declares side, source platform, unit/case, generation contract, source and artifact
hashes, input snapshot and parameter hashes, columns/types/nullability, comparison
mode, rows and capture provenance. No missing bindings or column mappings are
invented. Both documents must agree on these declarations before comparison.

Types: INT64, DECIMAL, STRING, BOOLEAN, DATE and TIMESTAMP_UTC. Decimal values
must be exact strings without exponents, with at most 38 significant digits.
UTC timestamps must explicitly specify `Z` or `+00:00`, with at most microsecond
precision. Arbitrary floats, local timestamps, implicit casts, tolerance-based
comparison and unbounded data are unsupported. Limits: 8 MiB per input file,
10,000 rows and 256 columns.

MULTISET comparison preserves duplicates and nulls; ORDERED also checks row
position. Reports separate MATCH, MISMATCH and INCOMPARABLE, and record missing/
extra counts without copying business row values into report diagnostics.
Bindings and capture metadata remain operator attestations, not authenticated
proof of which engine executed a capture. Matching a case is not a package-wide
semantic-coverage result.

```bash
python -m ssis_migration.validator.reconciliation \
  --source-observation examples/validation/source_observation.json \
  --target-observation examples/validation/target_observation.json \
  --output /tmp/new-reconciliation.json
```

These examples are synthetic, so MATCH still reports source equivalence as
NOT_VERIFIED and migration_accepted as false. The CLI refuses to overwrite an
existing output. For acceptance, keep original captures in the evidence report
folder; report paths must remain contained, and every SHA-256 is rechecked.

The workbench validation tab also accepts both observation files and downloads
a portable ZIP containing the comparison and original bytes. It does not infer
weights, criticality, execution success or migration acceptance from a MATCH.

## Controlled C# verification

This continuation installed official .NET SDK **8.0.426** into temporary storage,
verifying the archive against Microsoft's published SHA-512 release metadata.
All eight real compiler integration tests passed: six bounded differential
fixtures and two negative compilation/reference checks.

```bash
SSIS_TEST_DOTNET=/absolute/path/to/dotnet \
  python -m pytest -q tests/generator/test_csharp_compiled.py
```

The executed sources are repository-controlled fixtures with explicit test-only
Dts stubs. No uploaded script or destructive source workload is executed.
This verifies fixture-stub compilation and observed cases, not real SSIS runtime
binding, arbitrary C# coverage or general migration equivalence.
