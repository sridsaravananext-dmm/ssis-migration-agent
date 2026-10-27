# Legacy ETL Migration Agent

**Last updated date - 10/27/2026**

Next work uses genuine exported projects and their dependencies. See the
[source files requested](docs/source-export-request.md) for each platform, the
current support limits and what to send first. The workbench also shows and
downloads this checklist. New synthetic source packages, metadata and expected
results will not be authored; earlier regression demos do not establish migration
acceptance on real workloads.

[Existing public exports](docs/public-source-candidates.md) can also be used.
The first imported source is an unchanged licensed PowerCenter mapping/workflow
with pinned provenance. Its Windows-1252 declarations now parse and replay with
original byte hashes. [Explicit dependency selection](docs/powercenter-dependency-binding.md)
now binds the workflow session to its mapping using matching repository, folder
UUID and export version. Unsupported behavior and other missing dependencies
remain explicit. See the [audit](docs/public-powercenter-audit.md).

[PowerCenter behavior analysis](docs/powercenter-behavior-analysis.md) now extracts
33 requirements from the original bound exports, including native expressions,
router routes, sequence/lookup risks and session/writer settings. They appear in
contracts and the workbench with source evidence and unresolved items. Runtime
semantics, effective configuration and target translation remain unverified.

[Version 2 port-default analysis](docs/powercenter-port-defaults.md) preserves
missing and empty declarations separately and records the two original sequence
error expressions. Version 1 analyses and contracts remain replayable; no runtime
fallback or error outcome is invented.

[Version 3 configuration/binding analysis](docs/powercenter-configuration-bindings.md)
now preserves scoped property contributions and all four connection owners.
The lookup's differing declarations remain visible; effective configuration and
physical database/schema bindings stay unresolved until real metadata is supplied.

[Oracle's original ODI Smart Export](examples/public/oracle_odi_etl_offload/README.md)
is preserved with its license and pinned provenance. Its versioned
[ODI 12c normalizer](docs/odi-mapping-normalization.md) now builds IR for all seven
mapping declarations, explicit connector/attribute references and all original
typed fields. Source behavior version 3 adds typed date/window syntax to the
expression and procedure/KM analysis, retaining 247 evidence-backed requirements. The
workbench downloads those artifacts and a per-mapping Databricks assessment;
missing definitions, physical/KM semantics and target bindings still block complete
notebook generation. Earlier structural graphs and compatibility reports remain replayable.

Incremental, file-based legacy ETL migration framework. **Databricks is the only
current target.** DataStage and Ab Initio are explicitly excluded. Legacy server
connections are not required; source exports and dependencies are supplied as files.

| Source | Implemented capability | Explicit limits |
| --- | --- | --- |
| SSIS | Existing DTSX → IR → analysis → contracts → deterministic/AI generation → notebooks/jobs/DAB/report | Missing metadata, unsupported tasks and unverified behavior remain blockers |
| TM1 / Planning Analytics | MDX, rules/feeders, TurboIntegrator and dimensional/process metadata analysis; native IR and bounded Databricks candidates | Dense numeric leaf snapshots and exact integer arithmetic; broader runtime semantics remain unsupported |
| Informatica PowerCenter | XML IR, explicit workflow/mapping binding, native expression syntax and declared behavior requirements in contracts/reports | Runtime semantics, effective configuration, physical connections and target translation remain unverified/unimplemented |
| SAP Data Services / BODS | Bounded DI XML declarations and explicit object-ID references, native IR/contracts/report | ATL and undocumented XML/link encodings unsupported; no target translation |
| Oracle ODI | Legacy Sunopsis review plus versioned ODI 12.2.1 mapping IR, typed fields, scoped references/lineage, source requirements and Databricks capability assessment | New profile requires repository 05.02.02.07; unknown classes, missing definitions, physical/KM semantics and target translation remain blocked/unimplemented |

The [developer workbench](docs/developer-workbench.md) supports source upload or
paths, catalog, bronze/silver/gold schemas, runtime bindings, notebook destination,
cluster and artifact paths. Native XML runs download structural review artifacts;
SSIS and supported TM1 candidates also generate Databricks artifacts. App-local
artifacts should use `/tmp/...`; `/Workspace/...` is a notebook destination.

```bash
python -m pip install -r requirements-app.txt
python -m streamlit run app.py --server.port 8000
```

Human Databricks validation is deferred at the user's request; offline
implementation can continue. Deployment, live execution and source equivalence
are not implied by generation or by this decision. No workloads are deployed or
executed automatically.

[Offline evidence validation](docs/offline-evidence.md) now supports exact typed
observation comparison and replayable TM1 acceptance drafts. Nulls, duplicates,
decimal precision and declared row ordering are retained. Matching synthetic
fixtures earn no source-equivalence credit. Acceptance still requires assessed
weights/criticality, at least 80% weighted verified semantic coverage, every
critical behavior and all mandatory evidence gates.

Working branch: `GenericAITool-version01`.

[Same-cube leaf-rule candidates](docs/tm1-leaf-rule-generation.md) now support
literal TM1 DB cell reads with explicit dependency ordering. Cycles and dynamic
or cross-cube reads block generation; the existing constant-rule profile and
previous candidate utility bytes remain unchanged.

[Static public MDX subsets](docs/tm1-static-subset-generation.md) now expand from
supplied ordered metadata through `TM1SubsetToSet`. Private/dynamic subsets,
missing scope and ambiguous or invalid members block generation.

Full regression baseline (2026-10-27): **1,406 tests passed, 12 skipped**.
Four skips require a live Databricks connection; eight require the optional .NET
8 SDK, which is unavailable in this environment. Execution and real-source
equivalence remain unverified.

The ODI normalization increment passed **22 native checks**, **67 existing
XML/preflight/workbench checks**, and a complete real ZIP workbench run. All
41,411 original elements are retained as nodes or typed fields. New mapping
requirements cannot be removed through a generic plan; target assessments retain
the unresolved external references and both TRUNCATE options as blockers.
Installed-wheel API review and full IR/requirement/contract/assessment replay
passed after the source copy was removed. Three older vendor graphs/envelopes,
the original ODI compatibility report and six PowerCenter version 1/2 reviews
remain unchanged and replayable; original source/license hashes still match.

[ODI date/window analysis](docs/odi-date-window-analysis.md) now recognizes 45
native expressions and produces 44 independently parsed Databricks SQL review
fragments, including three window expressions. All 89 source records remain
visible. The date conversion retains its original index and format tokens but
has no target SQL until source/target date rules are established. Window defaults
and ordering uniqueness remain unverified. The earlier
[expression/KM increment](docs/odi-expression-km-analysis.md) retains all 80
procedure/KM steps, 20 options and 135 unresolved association records with their
source evidence and flags. Fragments earn no semantic coverage or migration
acceptance; complete notebook generation remains blocked.

This increment passed **18 focused checks** and the full regression. Installed-wheel
review and replay passed after source removal, including exact comparisons of
earlier behavior analyses, plans/contracts/assessments and 41 original SQL candidates.

The original ODI compatibility increment passed **30 focused checks**, the full
regression, and installed-wheel CLI/schema/replay checks outside the checkout.
Original ZIP/XML upload is inventoried and blocked before unsupported IR work;
six archived PowerCenter version 1/2 analyses and contracts still replay unchanged.
All original public source/license hashes match their pinned provenance.

The configuration/binding audit passed **50 focused checks** and **73 existing
checks** before the full regression. Six version 1/2 analyses and contracts remain
unchanged. Installed-wheel replay passed for all three profiles after source
removal, including the six previous archives; original source/license hashes
remain unchanged.

The port-default increment passed **33 focused checks** across defaults, legacy
behavior and the workbench. Three saved version 1 behavior/plan/contracts compare
exactly and replay. The installed wheel replays both profiles after removing the
source copies. Original public source and license hashes remain unchanged.

The declared-behavior increment passed **20 focused behavior/workbench checks**
and **172 existing checks** before the full regression. Six prior structural
graphs and generic plan/contracts remain exactly unchanged and replayable.
Installed-wheel behavior and contract replay passed after both source copies
were removed, including validation of the six older review artifacts.

The explicit PowerCenter dependency increment passed **25 binding checks** and
a real-export workbench selection check. All five prior one-file graphs remain
exactly unchanged. CLI and installed-wheel review/replay passed after both source
copies were removed; the resulting review conserves 320 nodes and 382 relationships.

The public PowerCenter increment passed **59 focused XML/intake checks**, exact
before/after comparison of all three existing UTF-8 XML graphs, the documented
CLI command, and installed-wheel review/replay after removing the original source
copies. Public source bytes and license hashes remain unchanged.

The 2026-10-26 real-source guidance update passed **56 existing workbench checks**,
all five platform checklist screens and wheel-content verification. No new
source examples, metadata fixtures or expected results were authored.

Implementation details and remaining scope:

- [XML source adapter profiles](docs/xml-source-adapters.md).
- [TM1 Databricks candidates](docs/tm1-databricks-candidate.md).
- [Offline continuation and remaining capabilities](docs/remaining-phases.md).
- [C# compilation and observed-case comparison](docs/phase7-csharp-compilation-equivalence.md).

Run regression tests with `python -m pytest -q`. Optional real C# fixture tests
require a .NET 8 SDK and `SSIS_TEST_DOTNET=/absolute/path/to/dotnet`.
Source runtime equivalence remains scoped to the observations actually supplied.
