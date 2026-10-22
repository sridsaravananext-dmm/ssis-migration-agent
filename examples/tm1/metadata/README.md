# Synthetic dimensional and process metadata

`synthetic_catalog.json` is a parser/binding fixture, not metadata from a real
TM1 server. It declares two dimensions, a Sales cube, a static public subset,
an ASCII datasource, a NONE datasource and three process signatures. The
consolidated Product member retains weights `1` and `-0.5`; nothing evaluates
those weights or infers cell values. The source version deliberately remains
`null`.

The catalog uses the documented, versioned `tm1_metadata_v1` interchange format.
Native exports must be converted using supplied source evidence; this phase
does not guess their layouts. Each file declares its source system and exact
canonical identifier policy. Unknown fields are rejected. Every required
collection is present, even if empty. Use `null` only where the schema permits
an explicitly unknown value.

Generate a TI analysis and bind this metadata:

```bash
.venv/bin/python -m ssis_migration.adapters.tm1.ti \
  --manifest examples/tm1/ti/manifest.json \
  --output /tmp/new-ti-source-analysis.json
.venv/bin/python -m ssis_migration.adapters.tm1.metadata \
  --analysis /tmp/new-ti-source-analysis.json \
  --metadata examples/tm1/metadata/synthetic_catalog.json \
  --output /tmp/new-ti-metadata-binding.json
```

The binding CLI returns **1 / PARTIAL** for this fixture: 27 declarations match,
while source version, parameter-driven file location and row-driven Product
coordinate remain unresolved. Defaults stay declarations; `pInputFile` is not
replaced with its default to make a runtime path look resolved.

All 24 original source requirements remain in the report, along with source and
metadata hashes/pointers. A child-process signature does not supply its body or
prove completion. Metadata/Data passes remain separate and unverified. No file,
SQL or process reference inside the metadata is followed. No coverage or
migration acceptance is awarded.

See [the metadata schema](../../../src/ssis_migration/schemas/tm1_metadata.v1.json)
and [phase documentation](../../../docs/phase8-tm1-metadata-binding.md) for
declaration fields, supported bindings and remaining requirements. Existing
output paths are never overwritten.
