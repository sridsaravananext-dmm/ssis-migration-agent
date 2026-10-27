# Original Oracle ODI ETL Offload export

The files under `upstream/` are unchanged originals from
[Oracle's data-warehouse-etl-offload-samples repository](https://github.com/oracle/data-warehouse-etl-offload-samples/tree/4f67a1ccf84c6a4ef02720690cdee61af3027f25).
The original Oracle copyright and UPL-1.0 license are retained.
[provenance.json](provenance.json) records the pinned commit, original paths,
URLs, byte sizes and SHA-256 hashes, including the unmodified XML archive member.

No source data, expected output rows, replacement metadata or native export was
authored for this increment. The upstream README is context for the source
sample; its deployment/installation instructions are not run by the migration tool.

Upload the original ZIP through **Upload a source bundle (.zip)** in the workbench.
Select its single XML member. The **ODI export compatibility** panel downloads
the original legacy-profile inventory. The versioned ODI 12c mapping normalizer
now permits **Run Migration** for IR/contract review. Its mapping requirements
and Databricks generation assessment are downloadable after the run. You can
also upload the extracted XML directly.

For command-line inventory, extract the archive with the existing bounded intake:

```bash
python - <<'PY'
from pathlib import Path
from ssis_migration.ui import extract_bundle
archive = Path('examples/public/oracle_odi_etl_offload/upstream/CloudAtCustomerODIETLOffload/ODISmartExport_ETLOffload_BigDataCloud@Customer.zip')
extract_bundle(archive.read_bytes(), '/tmp/oracle-odi-source')  # New folder required.
PY
python -m ssis_migration.adapters.odi.preflight \
  --source '/tmp/oracle-odi-source/ODISmartExport_ETLOffload_BigDataCloud@Customer.xml' \
  --output /tmp/oracle-odi-compatibility.json
```

Use a new output filename. Successful report writing returns exit code 0; check
`structural_parser.eligibility` and `status` for compatibility/migration status.
Invalid input, preflight resource limits or an existing output file return 2
without writing a partial compatibility report.

This export declares ODI `12.2.1`, repository `05.02.02.07` and ISO-8859-1.
It contains 41,411 elements and 2,495 objects in 45 classes. The original legacy
structural parser remains blocked by its encoding/10,000-element limits. The
new `odi_12c_mapping_declarations_v1` profile preserves all original elements as
2,500 IR nodes plus 38,911 typed field declarations, with exact byte replay and
explicit mapping links. It adds seven mapping requirements plus one export
review requirement. This does not translate native physical/KM behavior or
establish migration acceptance.

The latest requirements profile (version 3) retains the version-2 procedure/KM
analysis and adds bounded native date/window syntax, producing 247 requirements.
All 89 expression records are retained: 45 parsed native expressions, 37 empty
declarations and seven layout records. Candidate version 2 has 44 Databricks SQL
syntax review fragments in `target_expression_candidates.json`, including three
LAG/MAX/SUM windows. Their omitted defaults and ordering uniqueness remain
unverified. The date AST preserves substring start `0`, length `10` and format
`'YYYY-MM-DD'`; no date SQL is generated until source/target date rules are
established. All 80 command
steps, 20 options and 135 unresolved association records retain source evidence.
No templates/commands are executed and no effective option or default is invented.
See [date/window syntax and limits](../../../docs/odi-date-window-analysis.md)
and [the earlier version-2 scope](../../../docs/odi-expression-km-analysis.md).

To produce the new review from the same extracted source:

```bash
python -m ssis_migration.migrate \
  --source '/tmp/oracle-odi-source/ODISmartExport_ETLOffload_BigDataCloud@Customer.xml' \
  --output /tmp/oracle-odi-review > /tmp/oracle-odi-review-run.json
```

The final status is `BLOCKED` with zero generated notebooks. The report and
`target_generation_assessment.json` identify missing definitions and unimplemented
generation capabilities per mapping. In the original, 52 KM references, 32 native
datatype references, seven column references and one datastore reference remain
unresolved under strict class/key/GUID binding. These are reference counts, not
counts of distinct missing objects. The source also retains both TRUNCATE options
and every declared physical design; no design is automatically selected.

The inventory omits field values and encryption key material. It contains exact
source SHA-256/size and representative XML locators. Replay requires unchanged
source bytes, which may be relocated:

```python
import json
from ssis_migration.adapters.odi.preflight import validate_preflight

with open('/tmp/oracle-odi-compatibility.json') as stream:
    report = json.load(stream)
validate_preflight(report, '/tmp/oracle-odi-source/ODISmartExport_ETLOffload_BigDataCloud@Customer.xml')
```

See the [normalization audit and next work](../../../docs/odi-mapping-normalization.md)
and the [earlier compatibility checkpoint](../../../docs/public-odi-audit.md).
