# Synthetic TM1 source bundle

`manifest.json` explicitly groups the existing synthetic MDX, rule and TI
examples with their supplied metadata catalog. It is an interchange manifest,
not a native IBM export. The examples deliberately lack some cube/member/source
version definitions; the tool preserves those gaps rather than filling them in.

```bash
.venv/bin/python -m ssis_migration.migrate \
  --source examples/tm1/bundle/manifest.json \
  --source-platform tm1 \
  --output /tmp/tm1-review-results
```

Each run creates a new `tm1-review-*` subdirectory with source evidence, native
IR v2, a review plan, migration contracts, accounting, validation and report JSON.
Expected structure: nine supplied files, three source units, 254 nodes and 394
edges. All 648 graph records become unverified preservation obligations.

The process is **BLOCKED for migration acceptance**. No target code, notebook,
job or DAB is produced. Execution/data equivalence and source semantic coverage
remain unverified. This command never connects to TM1 or executes source SQL,
commands, processes or Databricks workloads.

Paths resolve against each declaring manifest. SHA-256 hashes identify exact
input bytes; update the appropriate TI section, TI manifest and outer manifest
hashes whenever those files change. Unit order is inventory order, not execution
order. Raw proprietary `.pro` layouts are unsupported. Missing roles, conflicting
metadata, stale hashes and ambiguous bindings remain explicit blockers.

See [native integration details](../../../docs/phase8-tm1-native-integration.md)
and [the bundle schema](../../../src/ssis_migration/schemas/tm1_source_bundle.v1.json).
