# Legacy ETL Migration Agent

**Last updated date - 10/26/2026**

Next work uses genuine exported projects and their dependencies. See the
[source files requested](docs/source-export-request.md) for each platform, the
current support limits and what to send first. The workbench also shows and
downloads this checklist. New synthetic source packages, metadata and expected
results will not be authored; earlier regression demos do not establish migration
acceptance on real workloads.

Incremental, file-based legacy ETL migration framework. **Databricks is the only
current target.** DataStage and Ab Initio are explicitly excluded. Legacy server
connections are not required; source exports and dependencies are supplied as files.

| Source | Implemented capability | Explicit limits |
| --- | --- | --- |
| SSIS | Existing DTSX → IR → analysis → contracts → deterministic/AI generation → notebooks/jobs/DAB/report | Missing metadata, unsupported tasks and unverified behavior remain blockers |
| TM1 / Planning Analytics | MDX, rules/feeders, TurboIntegrator and dimensional/process metadata analysis; native IR and bounded Databricks candidates | Dense numeric leaf snapshots and exact integer arithmetic; broader runtime semantics remain unsupported |
| Informatica PowerCenter | XML declarations, scoped instances, port lineage, workflow links, native IR/contracts/report | Structural analysis only; target translation remains unimplemented |
| SAP Data Services / BODS | Bounded DI XML declarations and explicit object-ID references, native IR/contracts/report | ATL and undocumented XML/link encodings unsupported; no target translation |
| Oracle ODI | Sunopsis object/field declarations and class-scoped key references, native IR/contracts/report | Unknown classes, knowledge-module execution and target translation remain unsupported |

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

Last full regression baseline (2026-10-22): **1,244 tests passed, 4 live Databricks checks skipped**.
Installed-wheel and previous-candidate replay checks passed; execution and source
equivalence remain unverified.

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
