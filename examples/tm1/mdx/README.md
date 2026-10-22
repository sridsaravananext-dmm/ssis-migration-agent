# Synthetic MDX example

`sales_axes.mdx` demonstrates axes, NON EMPTY, a named subset and a tuple slicer.
Its cube/member/subset names are illustrative, not supplied TM1 metadata.

Analyze it offline:

```bash
.venv/bin/python -m ssis_migration.languages.mdx \
  --source examples/tm1/mdx/sales_axes.mdx --dialect tm1 \
  --output /tmp/new-mdx-analysis.json
```

The output preserves the query structure and reports eight missing metadata/context
dependencies. Structural success is not a faithful Databricks translation or
migration acceptance. No query or generated workload executes. The TM1 package
adapter, rules/feeders and TurboIntegrator parsing remain separate future work.
