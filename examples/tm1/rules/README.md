# Synthetic TM1 rules and feeder example

`sales_rules.rux` demonstrates ordered calculations, N qualifiers, DB coordinates,
STET/CONTINUE, directives and a declared feeder. Names and expressions are
illustrative. No real source cube, TM1 version or dimensional metadata is supplied.

```bash
.venv/bin/python -m ssis_migration.adapters.tm1.rules \
  --source examples/tm1/rules/sales_rules.rux \
  --format plain-rux-text --output /tmp/new-rules-analysis.json
```

The analyzer reports six ordered statements, one unresolved feeder relation and
16 absent metadata/context dependencies. It does not infer a source cube from
the filename, evaluate rules, expand feeders or generate Databricks code.
Structural success is not TM1 execution validity or migration acceptance.
