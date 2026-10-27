# Offline implementation continuation

As of 2026-10-27, the next adapter and semantic work must use real user-supplied
exports or existing public exports with verified reuse terms and provenance.
Do not author new synthetic source packages, metadata, input datasets or
expected outputs. Existing regression examples remain demonstrations, not proof
of real workload compatibility. See [the source file request](source-export-request.md).

Next implementation: add versioned, design-scoped physical dependency analysis
from the original export's 11 design, 48 physical-node, 25 execution-unit and
25 execution-group declarations. Preserve alternative designs and exact scoped
references without choosing a design, execution order or effective KM option.
Missing exact module/metadata definitions and explicit target bindings remain
blockers. Establish date/calendar/index and window-default/order semantics,
extend component/type/null analysis and verify KM identity/option/template
behavior before target translation. The original Oracle mappings now have
normalized declarations and
evidence-backed review contracts. PowerCenter effective configuration and
physical binding still require exact product-version, connection/DDL and run
metadata; do not guess them while continuing independent ODI work. Add supported
Databricks translation capabilities against real evidence. Use actual source-run
inputs/outputs when available; the upstream workflow log alone is not equivalence
evidence. PowerCenter/BODS/ODI target
generation and broader TM1/C# semantics remain unfinished. Do not mark these
phases complete from structural analysis.

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
8. Same-cube leaf-rule generation: **implemented** for explicit literal DB reads
   and acyclic complete-coordinate N rules. Dependency ordering, source evidence,
   standalone notebooks and acceptance drafts are covered; existing constant-rule
   artifacts retain their utility bytes. See [capability details](tm1-leaf-rule-generation.md).
9. Static public MDX subsets: **implemented** using explicitly supplied ordered
   metadata and public scope. Expansions carry source/declaration evidence and
   compose with bounded Head/Tail/CrossJoin. Private/dynamic/ambiguous subsets
   remain blocked. See [capability details](tm1-static-subset-generation.md).
10. Public PowerCenter export compatibility: unchanged licensed mapping/workflow
    originals and upstream log are imported with pinned URLs and byte hashes.
    Explicit Windows-1252 declarations now parse and replay; existing UTF-8 graphs
    are unchanged. All 320 original XML elements survive into review obligations.
    **Encoding/structural compatibility implemented.** Multi-file binding is
    covered below; transformation semantics remain pending. See the [source audit](public-powercenter-audit.md).
11. Explicit PowerCenter dependency binding: **implemented** for session-to-mapping
    references with matching repository details, folder UUID and export version.
    CLI and workbench selection preserve original files, ambiguous/missing links,
    unsupported constructs and full review obligations. Replay validates every
    snapshot and original version declaration; single-file graphs are unchanged.
    See [usage and limits](powercenter-dependency-binding.md). Broader dependency
    types and target generation remain pending.
12. PowerCenter declared behavior requirements: **implemented** as a separate,
    versioned analysis with bounded native expression syntax. The real bound
    exports produce 31 evidence-backed requirements covering transformations,
    router groups, sequence/lookup risks, target load ranks, session transactions,
    configuration/overrides and writer/connection policies. The contract bridge
    preserves every requirement without semantic verification or coverage credit.
    Old structural graphs and generic contracts are unchanged. See
    [behavior analysis and limits](powercenter-behavior-analysis.md).
13. PowerCenter port defaults: **implemented** in a version 2 behavior profile.
    Missing/empty/nonempty declarations stay distinct; the original two sequence
    `ERROR` defaults enter separate contracts, for 33 requirements total. Effective
    defaults, invocation and failure scope remain unverified. Recorded version 1
    analyses/contracts still replay; unsupported profiles do not fall back.
    See [port-default analysis](powercenter-port-defaults.md).
14. PowerCenter configuration/binding audit: **implemented** in behavior profile
    version 3. Selected configuration declarations and nine instance scopes retain
    their separate property contributions; four connection references retain
    distinct ownership and explicit physical-metadata gaps. Differing declarations
    do not select a winner. Product version, effective configuration and physical
    database/schema bindings remain unverified/unresolved. Versions 1/2 replay
    unchanged. See [configuration and binding readiness](powercenter-configuration-bindings.md).
15. Oracle ODI public-export audit and compatibility preflight: **implemented**.
    The unchanged, licensed Oracle ZIP has pinned original/member hashes. Its
    41,411 XML elements, ISO-8859-1 declaration and unsupported modern classes
    are inventoried separately from IR. CLI and ZIP/XML workbench flows produce
    a replayable compatibility report; an ineligible structural run is disabled.
    Existing parser limits and eligible native review remain unchanged. Modern
    mapping normalization and KM semantics are not implemented by this inventory.
    See [the source audit](public-odi-audit.md).
16. ODI 12c mapping normalization and generation assessment: **implemented as
    declaration/contract analysis** for explicit ODI 12.2.1 / repository
    05.02.02.07 Smart Exports. All 41,411 elements survive as 2,500 nodes and 38,911
    typed fields; 12 scoped data-flow links and 50 declared attribute-reference
    lineage links are retained. Seven mapping requirements plus an export review
    requirement bind into contracts. Per-mapping Databricks assessments retain
    missing definitions, physical designs/KM behavior, target-binding gaps and
    both TRUNCATE options as blockers. No notebooks or execution are enabled.
    Earlier structural/preflight profiles remain unchanged. See
    [normalization, limits and remaining generation work](odi-mapping-normalization.md).
17. ODI bounded expression syntax, SQL review fragments and procedure/KM
    declarations: **implemented** in source behavior version 2. All 89 expression
    records remain visible; 41 bounded fragments pass independent target syntax
    checks. Date/window forms, effective defaults and all runtime semantics remain
    unverified. Four procedure/KM definitions, 80 steps, 20 options and 135
    association records enter contracts, totaling 247 source requirements.
    Differing module IDs remain unresolved; no command/template is executed and
    no physical design, runtime order or option value is chosen. Version 1 and
    native IR remain unchanged. See [scope and limits](odi-expression-km-analysis.md).
18. ODI date/window syntax and window SQL review: **implemented** in source
    behavior version 3, native syntax version 2 and target-candidate version 2.
    All 89 records remain visible: 45 parsed native expressions, 37 empty
    declarations and seven layout records. Three LAG/MAX/SUM window fragments
    raise the target syntax count to 44. Omitted sort/null directions, LAG
    arguments and frame endpoints remain omitted; ordering uniqueness is
    unverified. The date AST preserves `0`, `10` and `'YYYY-MM-DD'` without
    generating date SQL. The native IR and 247 requirement count are unchanged;
    earlier behavior/candidate profiles remain available for recorded-profile
    replay. Whole-workflow generation, execution and semantic acceptance stay
    blocked. See [implementation and limits](odi-date-window-analysis.md).

These are incremental capability additions. Structural vendor adapters are not
full vendor migration engines. Broader TM1 behavior and vendor transformations
must gain explicit capability rules and semantic fixtures before generation.
Missing real exports or runtime observations must remain visible in reports.

Still requiring implementation/evidence: production PowerCenter/BODS/ODI target
translation and version-specific semantics; native BODS ATL and unsupported ODI
classes/KMs; wider TM1 consolidation, feeder, sparse/empty-cell, dynamic/cross-cube/cyclic rule
and process-lifecycle behavior; real SSIS runtime references/observations; live
Databricks controlled execution and independent source observations; assessed
business criticality/weights and data-equivalence coverage. These have not been
declared complete or waived by deferring a human Databricks review.
