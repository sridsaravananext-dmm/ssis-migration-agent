# Existing public PowerCenter SCD1 export

Original files from [Praveen-pa/Informatica-Powercenter](https://github.com/Praveen-pa/Informatica-Powercenter/tree/43a58b9629b767454e5622913d12f53335866ea2/SCD1),
commit `43a58b9629b767454e5622913d12f53335866ea2`. No source file was authored,
rewritten or sanitized for this repository. [provenance.json](provenance.json)
records pinned download URLs, original sizes and SHA-256 hashes. All original
bytes, Windows-1252 declarations, CRLF, vendor notices and the upstream
[MIT license](upstream/LICENSE) are retained. Git text normalization is disabled
for `upstream/` so that these hashes survive checkout.

- `upstream/SCD1/m_scd1.XML`: native mapping, source/target definitions, six
  transformations, nine instances and 26 connectors.
- `upstream/SCD1/w_scd1.XML`: separate native workflow, session, task instances,
  scheduler and configuration declarations; references mapping `m_scd1`.
- `upstream/SCD1/w_scd1_log.txt`: upstream workflow log, supplied as context.
  Its successful run messages are not independently bound output observations.

From the project root, review one original file at a time:

```bash
python -m ssis_migration.migrate --source examples/public/powercenter_scd1/upstream/SCD1/m_scd1.XML --output /tmp/public-powercenter-mapping
python -m ssis_migration.migrate --source examples/public/powercenter_scd1/upstream/SCD1/w_scd1.XML --output /tmp/public-powercenter-workflow
```

The workbench also accepts either XML file or its source path. Reports remain
**BLOCKED / REVIEW_ONLY**: target translation is unimplemented; unsupported
elements are preserved. The single-file workflow review correctly reports
`m_scd1` as missing even though the mapping is available as a neighboring file.
Cross-file dependency binding is a separate implementation increment.

No input rows, expected outputs, missing connection bindings or target schemas
have been invented. These files contain only ASCII-range bytes despite declaring
Windows-1252; they establish compatibility with this declaration and exact replay,
not evidence for every character or export-version variant. The source audit is
in [public-powercenter-audit.md](../../../docs/public-powercenter-audit.md).
