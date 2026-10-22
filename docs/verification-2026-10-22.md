# Offline continuation verification — 2026-10-22

Working branch: `GenericAITool-version01`.

Latest follow-up: [static public subset generation](tm1-static-subset-generation.md)
passed **1,244 tests, with 4 live Databricks checks skipped**, in 428.29 seconds.
The 29 new cases cover ordered metadata expansion, scope/privacy boundaries,
set-function composition, invalid metadata, evidence tampering, reports,
standalone generation, ZIP intake and acceptance drafts. Installed-only wheel
checks passed eight synthetic cases across three demos. Archived candidates with
437 and 98 obligations still replayed successfully; target runtimes are unchanged.

Previous follow-up: [same-cube leaf-rule generation](tm1-leaf-rule-generation.md)
passed **1,215 tests, with 4 live Databricks checks skipped**, in 397.27 seconds.
The 25 new cases cover rule reads/dependencies, blocked behavior, compatibility,
standalone and mixed generated kernels, fixture gating and acceptance drafts.
Installed-only wheel checks passed six synthetic cases across both demos.
The preceding commit's archived candidate replayed all 437 obligations unchanged.
The table below records the earlier offline-continuation checkpoint.

| Check | Result and scope |
| --- | --- |
| Full regression | **1,184 passed, 4 skipped**, 360.18 seconds. The four skips require live Databricks provider credentials. |
| Real C# integrations | **8 passed** with official .NET SDK 8.0.426 after published SHA-512 verification; six bounded compiled-C#/Python differential fixtures plus missing-symbol/reference checks. These also ran in the full suite. |
| Final intake/adapter regression | **115 passed** after the final evidence-identity and XML declaration guards. Includes existing SSIS intake/adapter behavior and protection for declaration-like text in comments/CDATA. |
| Final acceptance/reconciliation regression | **114 passed** after adding immutable candidate-directory guards and rejecting attested PASS reports without replayable engine captures. |
| Workbench/validation/adapter checkpoint | **136 passed** before final guard-only changes, including uploaded native XML analysis and observation-comparison app tests. |
| Installed-only wheel | All three XML adapters, TM1 candidate generation, four target fixtures, 437 preserved obligations, strict observation schema/comparison, native acceptance drafts and unassessed acceptance blocking passed outside the checkout. |
| Portable evidence | Removed the copied original TM1 source folder; the archived candidate still passed source/contract/code/artifact replay and acceptance-draft preparation. |
| Packaging and whitespace | Wheel built without network dependency resolution; tracked/new changes passed whitespace checks. |

The final focused runs cover six tests added after the full regression was
collected, plus regressions for every code path changed afterward. Results are
reported separately rather than presenting them as one full-suite invocation.

No source server or live Databricks workspace was contacted for migration
execution. No notebooks/jobs were deployed and no production data was written.
The compiler tests execute only repository-controlled fixtures and test-only Dts
stubs. Observed-case fixture equivalence is not real SSIS runtime equivalence.

New vendor XML examples are synthetic structural fixtures. PowerCenter/BODS/ODI
target translation remains unimplemented; wider TM1 semantics, real vendor export
compatibility and weighted source-equivalence acceptance remain unverified.
Human Databricks validation is deferred, not recorded as passing.
