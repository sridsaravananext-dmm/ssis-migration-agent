# Offline continuation verification — 2026-10-22

Working branch: `GenericAITool-version01`.

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
