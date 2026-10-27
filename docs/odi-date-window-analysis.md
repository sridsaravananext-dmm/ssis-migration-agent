# ODI date and window declarations

Architecture audit: 2026-10-27. Use the unchanged Oracle Smart Export already
stored with [pinned provenance](../examples/public/oracle_odi_etl_offload/README.md).
No source workflow, metadata, input rows or expected workload results are authored.

This records the phase-18 increment: behavior version 3, 247 requirements and
candidate version 2's 44 SQL review fragments. The following
[physical dependency increment](odi-physical-dependency-analysis.md) adds behavior
version 4 and 258 requirements. Its candidate version 3 retains the same 44
fragments and source syntax profile; the earlier profiles described here remain
available for replay.

The four remaining expression forms are one TO_DATE/SUBSTR conversion, LAG over
partition/order declarations, MAX with `ROWS 3 PRECEDING`, and partitioned SUM.
Their native reference identities and text are available; source runtime settings,
types, error behavior, effective defaults and ordering uniqueness are not verified.

| Existing interface | Smallest next change | Acceptance and risk |
| --- | --- | --- |
| `adapters/odi/expressions.py` syntax version 1 | Reuse its exact binding/evidence preparation unchanged; add a version-2 parser in a separate module | Preserve 41 earlier syntax forms and all version-1 analyses. Extended syntax must still validate both source text declarations and every reference. |
| `behavior_v2.py` and `behavior.py` | Keep version 2 frozen; add behavior version 3 and explicit recorded-profile dispatch | Old requirements, plans, contracts and SQL candidates replay exactly after source removal. Unknown profiles fail. |
| `generator/odi/expressions.py` candidates version 1 | Reuse simple-expression rendering, target SQL analysis and contract accounting; add a separate window candidate profile | Preserve omitted LAG arguments, sort/null directions and frame endpoints. Do not serialize parsed windows back through a formatter that inserts defaults. |
| Source/candidate schemas, review pipeline and workbench | Add strict version-2 schemas and route the new behavior profile explicitly | All 89 source records remain visible. No date-format/index rewrite, physical table invention, notebook generation, execution, semantic credit or acceptance follows from syntax success. |

The native MAX expression declares a single frame bound, not an explicit ending
boundary. A general SQL formatter rewrites it as `ROWS BETWEEN 3 PRECEDING AND
CURRENT ROW`; the target review fragment must preserve `ROWS 3 PRECEDING` instead.
Unspecified sort direction, null ordering, LAG offset/default and frame endpoints
remain unspecified. No tie-breaker or unique business key is inferred.

The date AST must retain substring start `0`, length `10`, and the original
`'YYYY-MM-DD'` format token. Source dialect/calendar, malformed-input behavior,
substring-index rules and the target date parser are not confirmed, so this phase
must not generate date SQL by guessing a format or converting `0` to `1`.

## Implemented increment

Source behavior profile `odi_12c_mapping_requirements_v3` retains the same 247
requirements and native IR. Its expression profile
`odi_native_expression_syntax_v2` accounts for all 89 original records: 45 parsed
expressions, 37 empty declarations and seven layout-text declarations. The parsed
forms are 37 attribute references, two equalities, two simple calls, three
windows and one date conversion. Syntax status does not verify source validity
flags, types, coercion, runtime defaults or semantic equivalence.

`adapters/odi/expressions_v2.py` reuses the frozen version-1 binding/evidence
analysis. Its bounded native grammar adds exact reference-based LAG/MAX/SUM
windows and the original TO_DATE/SUBSTR form. Both `Txt` and `ParsedTxt` must
agree after expansion of whole reference tokens; quoted strings remain opaque.
Every reference must be uniquely and exactly bound in its original scope and
used by the expression. Unsupported clauses, text disagreements and budget
failures retain the full declaration with a review finding. Native expressions
are never handed to the SQL parser.

`generator/odi/expressions_v2.py` emits
`odi_databricks_expression_candidates_v2`. Its 89 entries contain 44 SQL
expression candidates: the earlier 41 plus the original three windows. Generated
target SQL is independently parsed in the Databricks dialect, then checked for
unchanged argument/partition/order bindings and exact declared frame structure.
Window clauses are assembled from validated AST fields and quoted logical
references, preserving omitted syntax. Date conversion has an explicit
`NO_CANDIDATE` entry with the missing semantic evidence recorded.

The strict version-2 expression and candidate schemas are registered alongside
the unchanged version-1 schemas. `behavior.py` dispatches using the recorded
source profile; `export_pipeline.py` selects candidate version 1 for behavior
version 2 and candidate version 2 for behavior version 3. No existing IR,
provider, `migration_intent` or executable-generation interface is replaced.
Earlier behavior versions 1/2 and candidate version 1 remain available for replay.

Extended grammar limits are 16 partition keys and 16 ordering keys, nonnegative
integer tokens of at most seven digits and value 1,000,000, and 256 characters
per original date-format token. Existing bounds still apply: 512 expression
records, 256 references per expression, 4,096 syntax characters, 256 tokens and
depth 32, with raw declaration text bounded at 65,536 characters. No limit
failure authorizes partial or prefix translation.

The workbench continues to download `target_expression_candidates.json` from
**ODI expression syntax candidates**. Neither logical aliases nor syntax success
establish physical source/target tables, effective KM/option behavior or complete
mapping code. The source's 80 procedure/KM steps, 20 options and 135 unresolved
option associations remain visible. Whole-workflow status stays `BLOCKED`,
execution stays `NOT_RUN`, generated notebooks remain empty, verified semantic
coverage is unavailable and `migration_accepted=false`.

## Verification and remaining evidence

**18 focused checks passed**, including the real original ZIP workbench run.
They cover all 89 records, the original date tokens, omitted window fields,
target single-bound frames, parser budgets and unsupported/disagreeing syntax.
Corrupting copies of existing in-memory declarations/review artifacts verifies
that source replay rejects a rewritten date index, candidate replay rejects a
formatter-expanded frame, and schemas reject a semantic-verification claim.
No new source export, metadata, input dataset or expected workload rows are
authored for these checks.

Full regression: **1,406 passed, 12 skipped in 988.02 seconds**. Four skips need
a live Databricks connection; eight need the optional .NET 8 SDK, unavailable in
this environment. These skips do not establish execution or source equivalence.

The installed wheel was checked outside the checkout. Its public migration API
produced 247 requirements and 44 SQL review fragments from the unchanged original
XML. Full source/plan/contract/candidate and capability-assessment replay passed
after removing that temporary source copy. Archived behavior versions 1 and 2,
their plans/contracts/assessments, and the 41 SQL fragments from candidate
profile version 1 compare
exactly and replay with their recorded profiles after source removal. Original
archive/member, license and upstream README hashes remain unchanged.

Date SQL still needs confirmed source dialect/calendar, malformed-input policy,
substring indexing and target-format behavior. Window migration still needs
types, null/default semantics, ordering uniqueness and actual observations.
Physical mappings, missing module/reference definitions, effective options,
templates and transaction behavior remain blockers to whole-workflow Databricks
generation. Controlled target execution, source/data reconciliation, critical
business requirements and weighted acceptance remain unverified.
