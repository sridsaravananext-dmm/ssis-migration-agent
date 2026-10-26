# PowerCenter declared behavior and migration requirements

This increment uses the unchanged public SCD1 exports and the completed
[dependency binding](powercenter-dependency-binding.md). The source audit found
six transformation types, four router groups, tied target load ranks, a data-driven
session, two relational writers, configuration/override layers and four connection
references. Their declarations require separate treatment before target generation.

| Existing entry point | Responsibility reused | Minimal change and risk | Acceptance checks |
| --- | --- | --- | --- |
| PowerCenter adapter and bundle profile | Original parsing, scoped links and exact replay | Add separate behavior analysis; leave structural graphs and profile versions unchanged | Five prior one-file graphs and the prior bundle still replay unchanged |
| `adapters/informatica/` | Vendor-specific interpretation | Add a bounded native expression parser and declaration-based requirements | Original router/lookup/update expressions parse without SQL interpretation; unsupported syntax is retained for review |
| `migration/native_review.py` | Neutral review planning | Optional source behavior input, bound to IR and uniquely assigned to steps | All original graph obligations plus every new requirement are conserved |
| `migration/contract_bridge.py` and schemas | Versioned contract bridge | Add optional `source_requirement` obligations; keep `migration_intent` and previous contracts unchanged | Omitted/changed requirements or evidence fail validation; no target capability promotion |
| `adapters/export_pipeline.py` | Review reports and downloadable artifacts | Publish/replay behavior analysis and include its unresolved items | Workbench/CLI return explicit risks and original evidence; no generated notebooks or invented observations |

Declarations are not verified runtime semantics. Syntax parsing does not establish
null/comparison behavior, precision, lookup duplicate selection, sequence state,
configuration precedence, error handling or transactional equivalence. Writer
permissions do not prove that a mapping emits each enabled operation. A successful
upstream log does not supply bound input/output observations.

No new source packages, metadata, input rows or expected workload results are
authored. Acceptance checks use original exports and hostile mutations of review
artifacts. Unsupported original constructs remain structural blockers.

Run the explicit-dependency CLI command in
[dependency binding](powercenter-dependency-binding.md). New PowerCenter reviews
include `source_behavior_analysis.json` alongside the existing IR, plan, contracts
and report. The workbench shows **PowerCenter behavior requirements** after a run
and provides a direct JSON download.

For the public workflow plus mapping, the pass produces **31 requirements**:
six transformations, four router groups, two target load ranks, one session, one
configuration, nine session transformation instances, two writers, four connection
references, and the source/target definitions. All 320 structural nodes and 382
relationships remain preserved separately. Each behavior requirement includes its
subject node, original declarations, XML evidence, preservation requirements and
unresolved items. No requirement receives verified semantic coverage, criticality
or weights automatically.

The native expression syntax profile recognizes declared port references,
`ISNULL`, bounded predicate negation, comparisons, `AND`/`OR`, parentheses and the
`DD_UPDATE` flag in update strategies. Limits are 4096 characters, 256 tokens and
32 nested syntax levels. Strings, arithmetic, parameters and other functions are
outside this profile and retain their text with a review-required result. The
parser never evaluates expressions, translates them as SQL or supplies missing
port metadata. Parsed syntax remains `NOT_VERIFIED` semantically.

Router output `REF_FIELD` declarations bind to unique declared input ports.
Unconnected output groups remain explicit, including the original unused default
group. Target load ranks retain ties; no serial order is derived from XML order.
Sequence values remain exact source strings, including the 64-bit end value.
Lookup duplicate behavior has no invented uniqueness constraint or winner rule.

Session declarations retain row mode and commit/rollback/recovery settings.
Configuration references may identify a unique same-file/folder declaration;
effective configuration remains unset. Base mapping, configuration, session,
instance and writer layers are preserved separately. Writer operation options
are recorded as declared strings; effective emitted operations remain unset.
Connection aliases require physical schema/database metadata before translation.

The new `source_behavior_analysis.v1.json` and `source_requirement.v1.json` schemas
are packaged for offline validation. Native review planning accepts optional
`behavior_analysis=...`, verifies the IR binding and assigns every requirement
exactly once. The shared contract bridge adds optional `source_requirement`
obligations, using their specific evidence, unresolved items and validation
requirements. `migration_intent`, old structural profiles, and generic contracts
without behavior analysis keep their existing representations.

`validate_export_review(..., behavior_analysis=...)` checks the published analysis
against the original exports. When the analysis argument is omitted, a new plan's
analysis binding triggers deterministic reconstruction. Older structural plans
remain readable as structural reviews. Removing individual requirements, changing
declarations or recomputing contract hashes cannot authorize altered behavior.

Remaining work: broader native expressions and defaults, effective configuration
and physical connection binding, verified transformation/runtime semantics,
explicit Databricks capability rules and target generation, then controlled
execution and independently bound reconciliation. Manual Databricks review remains
deferred; acceptance and critical-behavior gates are unchanged.

Verification on 2026-10-26: **1,291 passed, 12 skipped** in the full suite
(four live Databricks checks and eight optional .NET SDK checks). The 20 focused
behavior/workbench checks cover original declarations, native syntax and missing
port metadata, router field links and unconnected defaults, exact sequence values,
session/writer distinctions, requirement conservation, evidence validation and
self-rehashed tampering. Another 172 existing checks passed before the full run.
Six prior structural graphs and generic plan/contracts compare exactly with the
previous installed package. The new installed wheel replays the behavior/contract
artifacts after removing both source copies and also validates all six previous
generic review artifacts.
