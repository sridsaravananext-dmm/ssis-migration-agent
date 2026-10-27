# PowerCenter port defaults: versioned analysis increment

Audit on 2026-10-27: the unchanged public mapping contains 39 `TRANSFORMFIELD`
declarations with `DEFAULTVALUE`. Thirty-seven values are empty. The Sequence
ports `NEXTVAL` and `CURRVAL` both declare `ERROR('transformation error')`.
The current version 1 behavior analysis preserves these attributes but does not
classify them as separate error/default requirements.

| Existing entry point | Reuse | Minimal change and risk | Acceptance checks |
| --- | --- | --- | --- |
| `informatica/expressions.py` | Existing bounded native expression syntax | Retain version 1 behavior; add separate port-default analysis | Earlier syntax/results remain unchanged |
| `informatica/behavior.py` | Evidence-based requirements, layers and exact replay | Explicit profile dispatch; version 2 subclass enriches port defaults | Version 1 analyses and contracts replay byte-for-byte |
| `adapters/export_pipeline.py` | Review publication and validation | New runs use latest profile; replay uses recorded profile | No silent reinterpretation of old artifacts |
| Native plan/contract bridge | Existing source requirements and unverified status | Reuse unchanged; add requirements for nonempty port defaults | Two error declarations survive contracts; all graph obligations remain |

Missing, explicitly empty and nonempty default declarations are distinct. Do not
interpret an empty declaration as a NULL, empty string, zero, or a supplied
runtime fallback. An `ERROR` expression is a source declaration, not proof that
an error happened or that the whole job aborts. Its invocation and interaction
with reject, stop, transaction and recovery policies remain unverified.

Use the original exports and review-artifact tampering for regression checks.
No source packages, input rows, missing metadata or expected outputs are authored.
Effective configuration, physical metadata binding, target generation and source
equivalence remain separate unfinished capabilities.

New reviews use `powercenter_declared_behavior_v2`. Version 2 retains the 31
version 1 requirements and adds a `PORT_DEFAULT` requirement for each nonempty
default expression: **33 requirements** for the original workflow and mapping.
All 39 port-default declarations are also recorded on their owning transformations,
with 37 explicitly empty declarations and two explicit expressions. Empty metadata
adds no invented default value and no automatic semantic-coverage credit.

Each default records its declaration state (`ABSENT`, `EMPTY`, or `EXPRESSION`),
original text, optional syntax tree, unresolved items and source evidence.
`effective_default_value` and `runtime_outcome` remain unset. Nonempty defaults
receive separate contract obligations with the original port and transformation
context. The declared outgoing column links are retained: `NEXTVAL` has one and
`CURRVAL` has none. Neither fact proves when the source runtime evaluates a default.

The new bounded default syntax recognizes `ERROR('unescaped literal message')`
up to 4096 characters, including the exact original message. Escaping conventions,
dynamic messages and other function signatures are not guessed. Other nonempty
defaults pass through the existing bounded expression syntax profile; unsupported
or truncated text stays review-required, unchanged and unexecuted. Recognizing an
error expression does not authorize replacement with NULL or an eager job abort.

Version 1's syntax analyzer and declared behavior remain available:

```python
from ssis_migration.adapters.informatica.behavior import analyze_behavior

legacy = analyze_behavior(graph, profile='powercenter_declared_behavior_v1')
current = analyze_behavior(graph)  # powercenter_declared_behavior_v2
```

Validation reconstructs the exact profile recorded in the plan or analysis.
Unknown profiles fail explicitly instead of falling back to the latest version.
The common analysis/requirement schemas, structural IR, contract interface,
`migration_intent`, provider interfaces and generation gates are unchanged.

Use the existing explicit-dependency CLI command or workbench controls. The
behavior-analysis download and migration contracts now include the defaults and
the two error-expression requirements. Reports still show BLOCKED, NOT_RUN and
unverified semantic coverage; no PowerCenter target notebooks are generated.

Verification on 2026-10-27: **1,304 passed, 12 skipped** in the full suite
(four live Databricks checks and eight optional .NET SDK checks). The 33 focused
checks cover version 1 behavior, version 2 defaults, explicit error messages,
consumer links, missing/truncated review metadata, contract conservation,
unknown profiles and self-rehashed tampering. Three previous version 1 analyses
and their plans/contracts remain identical. Installed-wheel replay passed for
both profiles after removing source copies and for all three saved version 1
archives. Original source and license byte hashes remain unchanged.
