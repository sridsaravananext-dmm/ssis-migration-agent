# ODI expressions and procedure/Knowledge Module declarations

Architecture audit: 2026-10-27. Use the unchanged, licensed Oracle Smart Export
from [the public source](../examples/public/oracle_odi_etl_offload/README.md).
No source packages, metadata, input rows or expected workload results are authored.

The original has 89 expression records: 45 parsed expression declarations,
37 empty text declarations and seven layout-text records. Native `ParsedTxt`
uses `@{R0}` references, bound through explicit `SnpMapExprRef` identities.
The actual expressions include direct references, two join equalities, SUM,
YEAR, date conversion and three window expressions. None is assumed equivalent
to a target expression from its spelling alone.

The export also has four procedure/KM definitions, 80 command steps, 20 option
definitions and 135 command/option association records. Command technology and
language, text IDs, ranks, error/commit flags and encryption declarations remain
distinct source evidence. No procedure/KM command is executed or template expanded.

| Existing file/interface | Reuse and smallest change | Acceptance / risk |
| --- | --- | --- |
| `adapters/odi/native.py`, native IR and exact replay | Reuse unchanged typed fields and class/key/GUID links | Graph, snapshot, encoding, limits and version-1 reviews must remain identical. |
| `adapters/odi/behavior.py`, `MappingIndex` | Retain the version-1 analyzer; dispatch a new version-2 source-requirement profile | Version-1 analyses/contracts/assessments replay using the recorded profile; unknown profiles fail explicitly. |
| `adapters/odi/expressions.py` (new) | Bounded native reference/comparison/SUM/YEAR AST, with explicit source bindings and source/parsed-text consistency | Do not pass arbitrary native text to a SQL parser, evaluate it, fabricate columns or translate unsupported date/window semantics. Keep every record and literal null/empty declaration. |
| `adapters/odi/commands.py` (new) | Bind actual command/option/text declarations without choosing effective options, inherited commands or runtime order | Preserve all steps, associations, text, templates and flags, including unresolved/ambiguous bindings. |
| `generator/odi/expressions.py` (new) | Contract-bound Databricks SQL expression review fragments, independently parsed using the existing SQL analyzer | Fragments are syntax candidates, not executable notebooks, complete migrations, semantic verification or acceptance credit. Unknown expressions produce explicit unresolved entries. |
| Native review pipeline, schemas and workbench | Reuse plan/contracts, integrity and migration guards; add strict expression/candidate schemas and direct downloads | All 89 expression records and every command enter review; whole-workflow generation, deployment, execution and acceptance stay blocked. |

The first implementation scope is syntax and evidence-backed command analysis.
Source date parsing, window frames/order/null semantics, physical designs, KM
templates/options, transaction behavior and Databricks table bindings require
further implementation and real evidence before complete target generation.

## Implemented increment

Source behavior profile `odi_12c_mapping_requirements_v2` now carries 247 review
requirements: the original seven mapping and one export requirement, four
procedure/KM definitions, 80 command steps, 20 options and 135 option associations.
The native IR profile, original 41,411 elements, byte snapshots and version-1
requirements/contracts/assessments are unchanged. Replay selects the recorded
behavior profile; an unknown profile does not fall back to the newest version.

All 89 source expression records are retained. The bounded native syntax profile
recognizes 37 direct references, two equalities, one SUM and one YEAR. Bindings
require exact native reference keys, scoped attribute/component identities and
agreement between `Txt`, `ParsedTxt` and reference text. Source validity flags
such as `IsValid=0` remain unverified declarations. Token comparison does not merge
identifiers across whitespace or select a winner when text fields disagree.

Four expressions remain untranslated: the TO_DATE/SUBSTR conversion and three
window expressions. Thirty-seven empty declarations preserve literal `null`
parsed text, with default/inherited behavior unresolved. Seven declared layout
records retain their full text and remain outside executable expression syntax.
No declaration is converted into an invented SQL NULL, default value or no-op.

`odi_databricks_expression_candidates_v1` contains all 89 expression records,
with 41 Databricks SQL expression fragments. Each generated fragment is parsed
independently in the target dialect and checked for a single expression, no table
access and unchanged logical column bindings. The original native text is never
sent to the SQL analyzer. Source aliases are logical symbols; physical tables,
types, grouping, null/coercion and calendar behavior are not supplied or inferred.
SUM/YEAR fragments are syntactic candidates, not semantic-equivalence claims.

The procedure/KM analysis preserves both command sides, exact text identities,
original text and UTF-8 text hashes, template markers, ranks, language/technology,
option/commit/error and encryption declarations. There are 58 uniquely bound text
records across command sides; 55 contain ODI template markers. All 160 side-language
fields retain literal `null`. No template, source command, Groovy, SQL, procedure
or option expression is executed, expanded or assumed to be a confirmed SQL dialect.

All 135 association records name module IDs `139`, `329` or `18161`, which differ
from the supplied matching command/option module declarations. Exact line/option
identity candidates are retained with that difference; no matching module
definition or identity-remapping evidence was found for those association IDs.
Associations remain unresolved under this profile. This is a source declaration
comparison, not a claim that a vendor engine would reject or execute the export.
No effective option, command, runtime order or precedence is chosen.

Limits: 512 expression records, 256 references/expression, 4,096 syntax characters,
256 tokens and depth 32; raw expression text is bounded at 65,536 characters.
Procedure/KM analysis allows 256 definitions, 512 commands, 512 options, 2,048
association records and 2,048 text records, with 65,536 characters per analyzed
text field. Input-limit failures occur before review artifacts are published.

The workbench adds **ODI expression syntax candidates** and a direct JSON download.
The main source-analysis download contains all 247 requirements and original
command/association evidence. `target_expression_candidates.json` records syntax
status, logical bindings, unsupported entries and validation requirements.
The report keeps zero generated migration notebooks, `NOT_RUN`, no verified
semantic coverage and `migration_accepted=false`. Whole-mapping generation remains
blocked by unsupported semantics, missing definitions and physical/KM/target binding.

Verification: **19 focused checks passed**, including the complete original ZIP
workbench run. The full regression passed **1,389 tests with 12 skips** in
867.22 seconds. Four skips need live Databricks access; eight need the unavailable
optional .NET SDK. A final module-binding finding clarification passed two
targeted command/candidate checks. Original source/license byte hashes remain
unchanged. These checks provide syntax/integrity evidence, with no execution or
source-equivalence credit.

Installed-wheel API review passed outside the checkout. The final package
replayed source, contracts and all expression candidates after deleting the
temporary source copy, including the corrected option/module binding findings.
The frozen version-1 analysis, plan, contracts and capability assessment compare
exactly with the previous package and replay with their original paths absent.
