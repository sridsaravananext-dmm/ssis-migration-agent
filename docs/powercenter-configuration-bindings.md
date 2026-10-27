# PowerCenter configuration contributions and binding readiness

Audit on 2026-10-27: the original session explicitly references
`default_session_config`. The lookup's mapping property `Connection Information`
is `$Target`; its session-instance override declares `Oracle`. Four connection
references use alias `Oracle`, but no physical connection definitions are supplied.
The XML records repository/export versions, not the exact product version or a
verified precedence policy. Effective configuration cannot be asserted from these
facts alone.

| Existing entry point | Reuse | Minimal addition and risk | Acceptance checks |
| --- | --- | --- | --- |
| Behavior profiles 1/2 | Declarations, native defaults, evidence and contract replay | New version 3 subclass; preserve old profiles | Six original profile 1/2 analyses and contracts remain unchanged |
| Native graph and scoped instance links | Mapping/session binding and typed definitions | Resolve configuration contributions and connection ownership without altering IR | Keep instances distinct, bind exact declared names, expose ambiguous/missing references |
| Behavior requirements and contract bridge | Required behavior, unresolved items, immutable bindings | Enrich existing session/connection requirements with context and metadata gaps | Every declaration has XML evidence; no chosen precedence or invented database/schema |
| Review report/workbench | Downloadable analysis and required actions | Existing artifact displays detailed binding readiness | Original exports remain blocked until physical/configuration evidence and target capabilities exist |

This increment implements declaration binding and a configuration contribution
audit. It does not claim effective configuration, physical database binding or
support for an unseen connection-export format. Product version, connection
properties, schema/database mappings, parameter context and source/target DDL must
come from real exports or existing project documentation, never fabricated data.

New reviews use `powercenter_declared_behavior_v3`; versions 1 and 2 remain
available and replay using their recorded profile. The original bound workflow
still produces **33 requirements**, while its session and four connection
requirements now carry the contribution/binding audit. Structural IR and existing
contract/schema interfaces remain unchanged.

The session configuration reference binds only to a unique same-file, same-folder `CONFIG`
declaration of the declared `Session config` type. No selection is inferred from
`ISDEFAULT`, an object's name, or a neighboring export. Configuration-object and
session layers remain distinct. Mapping definitions, session-instance overrides
and session extensions are grouped by their actual mapping instance IDs.

Properties retain every original setting value and its layer/setting node IDs.
They are classified as a single declaration, repeated declarations, differing
declarations, or duplicates within a layer. These labels report exported facts;
they do not assert precedence. List order is presentation order, not an execution
or override strategy. Every effective value and effective configuration stays
unset until a verified policy can apply.

For the supplied exports, nine mapping-instance scopes remain separate. The
lookup records both `$Target` and `Oracle`. The two target instances share a
design-time definition but keep their distinct reject filenames, `target1.bad`
and `target21.bad`. This does not prove they use the same physical target.

The four connection references retain their owner roles/instances: `target`,
`target2`, `SQ_CUSTOMER` and `LKPTRANS`. The Source Qualifier's explicitly
associated source instance resolves to `CUSTOMER`, preserving the source's
declared owner `SYSTEM`, repository database name `source_db` and session table
override. Those declarations are not promoted to physical database/schema
bindings. Lookup table and connection selectors remain scoped contributions;
matching names do not manufacture physical identity.

Connection contexts report missing physical metadata and list the source exports
needed. Supplied `CONNECTION` containers in an unimplemented format are retained
as unsupported definition evidence instead of being interpreted as physical
connections. Empty parameter-file declarations retain their empty state, with
resolved values unset. Repository/export version `189.98` is not interpreted as
the product version.

Limits: 256 mapping-instance scopes per session, 64 configuration references,
64 configuration candidates, 64 layers per instance and 16,384 setting
contributions per context, in addition to existing XML/bundle limits. Exceeding
a limit fails explicitly before publishing partial artifacts.

Use the existing CLI/dependency or workbench flow. Download
`source_behavior_analysis.json` to inspect `configuration_contributions` on the
session requirement and `binding_context` on connection requirements. Missing
metadata requests also appear in the report's required actions and workbench
behavior panel. All semantics remain NOT_VERIFIED; the final review is BLOCKED,
execution is NOT_RUN, and no PowerCenter target notebooks are generated.

Files/context needed next for this sample:

- The exact PowerCenter product/build version and the configuration selected for
  the run; exported repository version alone is insufficient.
- Existing readable connection properties behind alias `Oracle`, covering the
  four contexts: technology, physical database/service and schema/owner mapping.
  Supply the actual native export or report format; credentials are unnecessary.
- Actual source/target DDL and applicable lookup/table overrides, plus the real
  parameter/environment values used. The existing declarations are useful
  evidence but do not replace missing physical/run metadata.

No live legacy connection is required. Physical binding, precedence evaluation,
verified runtime behavior, Databricks generation and independent reconciliation
remain unfinished; these are not waived by deferring a manual Databricks review.

Verification on 2026-10-27: **1,321 passed, 12 skipped** in the full suite
(four live Databricks checks and eight optional .NET SDK checks). The 50 focused
configuration/default/legacy/workbench checks include scoped differences,
separate writer contexts, connection ownership, associated source declarations,
missing mappings/references, duplicate settings, contribution limits and
self-rehashed fabrication attempts. Another 73 existing checks passed before
the full run. Six earlier version 1/2 analyses and contracts remain identical.
Installed-wheel replay passed for all three profiles after source removal and
for the six saved older archives. Original source/license hashes are unchanged.
