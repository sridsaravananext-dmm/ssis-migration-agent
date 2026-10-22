# Shared SQL reference fixtures

These two SQL files were supplied by the user in `tsql_plsql 1.zip` on
`GenericAITool-version01`, commit `6d5c939e9a5d22a8c71f3a0104bf628f01f2daa6`.
They are copied byte-for-byte; `provenance.json` records archive and entry hashes.

Use these as **offline parser/contract regression inputs**. The T-SQL sample
contains transactions, temporary tables, exception handling, procedure calls
and dynamic SQL. The PL/SQL sample includes a procedure, exception propagation,
a slash delimiter and a following SELECT. Neither is a verified migration or
a workload to run automatically.

The current relational analyzer explicitly reports their unsupported or
unresolved procedural behavior. Full procedure/control-flow translation is
future work. Parsing success would not establish equivalence or acceptance.
The prototype application, its FastAPI layer and its bundled Windows virtual
environment were reviewed as reference material and are not imported.
