# Synthetic TurboIntegrator section example

These four plain source files are a synthetic parser fixture, not a native `.pro`
export or a runnable migration. The manifest declares the process name only for
this fixture; the source server/export version remains `null`.

```bash
.venv/bin/python -m ssis_migration.adapters.tm1.ti \
  --manifest examples/tm1/ti/manifest.json \
  --output /tmp/new-ti-analysis.json
```

The version 1.0 manifest explicitly maps `prolog`, `metadata`, `data` and `epilog`
to UTF-8 source files and their raw-byte SHA-256 hashes. Relative paths resolve
against the manifest directory. Update a declared hash whenever source bytes
change. No headings, record IDs or filenames are used to guess section roles.
Omitted roles are reported as missing; an explicitly supplied empty file is an
empty section. Native serialized `.pro` decoding is not implemented.

The example preserves nine statement nodes and five declared side effects,
including separate synchronous and asynchronous child-process calls. Its 24
dependencies remain unresolved: no parameters, input-file schema, cube/dimension
metadata, child processes, file mappings or verified lifecycle were supplied.
Metadata and Data remain separate source-dependent record passes. `ItemSkip`
has an unresolved control target, never an assumed successful exit.

Exit 0 means bounded structural analysis completed. It does not establish TM1
server validity, semantic coverage, execution or migration acceptance. The tool
does not execute these files or follow any file, command or SQL references in
their expressions. The complete TM1 package adapter remains blocked.
