# Offline implementation continuation

The user removed the Databricks human-review checkpoint as a prerequisite to
further implementation on 2026-10-22. Deployment and live validation are not
part of this continuation. An implementation checkpoint is independent of the
mandatory execution, equivalence, critical-behavior and weighted-coverage gates.

Completed before this continuation: SSIS baseline protection, source intake,
versioned contracts, weighted acceptance, neutral IR, T-SQL/PL-SQL analysis,
bounded C# assessment/translation and compilation-evidence infrastructure,
TM1 MDX/rules/TurboIntegrator analysis and metadata binding, bounded Databricks
candidates, developer workbench, and app path/uploaded-metadata fixes.

Sequential increments in this continuation:

1. PowerCenter XML: source adapter, typed declarations, scoped dependency and
   column/workflow links, exact evidence replay, neutral contracts and reports.
   **Implemented.** Focused adapter/SSIS checks: 41 passed. The broader native
   review regression also passed after updating obsolete registry expectations.
2. SAP BODS: a separate XML structural profile; preserve unsupported ATL and
   undocumented XML behavior as blockers rather than inventing interpretations.
   **Implemented as a bounded structural profile.** Adapter/SSIS checkpoint: 47 passed.
3. Oracle ODI: Sunopsis object/field exports, declared object identities and
   references; preserve technology/knowledge-module semantics for review.
   **Implemented as a bounded structural profile.** Adapter/SSIS checkpoint: 57 passed.
4. Common offline validation: source/artifact replay, acceptance drafts,
   independent observation/reconciliation support; no synthetic-to-production
   equivalence promotion.
   **Implemented.** Native TM1 candidates replay before acceptance drafts; engine
   observation gates replay original captures and cannot be removed by policy.
5. Workbench integration and regression/packaging verification.
   **Implemented.** XML source discovery/routing and portable observation
   comparison are available in the app; validation is reported separately.
6. Bounded MDX generation extension: static CrossJoin/Head/Tail over complete,
   explicit leaf sets, with order preservation and strict input validation.
   **Implemented.** General functions and empty/sparse semantics remain unsupported.
7. Controlled C# compilation/equivalence: installed official .NET 8.0.426 after
   published SHA-512 verification. **Eight real integration tests passed.**
   This covers repository fixtures and test-only Dts stubs, not real SSIS runtime
   references or all source scripts.

These are incremental capability additions. Structural vendor adapters are not
full vendor migration engines. Broader TM1 behavior and vendor transformations
must gain explicit capability rules and semantic fixtures before generation.
Missing real exports or runtime observations must remain visible in reports.

Still requiring implementation/evidence: production PowerCenter/BODS/ODI target
translation and version-specific semantics; native BODS ATL and unsupported ODI
classes/KMs; wider TM1 consolidation, feeder, sparse/empty-cell, nonconstant rule
and process-lifecycle behavior; real SSIS runtime references/observations; live
Databricks controlled execution and independent source observations; assessed
business criticality/weights and data-equivalence coverage. These have not been
declared complete or waived by deferring a human Databricks review.
