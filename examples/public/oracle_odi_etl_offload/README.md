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
Select its single XML member. The **ODI export compatibility** panel downloads a
read-only inventory and explains why the structural run is disabled. You can also
upload the extracted XML directly.

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
It contains 41,411 elements and 2,495 objects in 45 classes. Structural IR is
blocked by both the unsupported original encoding and the 10,000-element parser
limit; modern mapping/KM classes also remain unsupported. Preflight does not
change those limits, build partial IR, resolve dependencies, generate notebooks,
execute commands or establish migration acceptance.

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

See the [architecture audit and next work](../../../docs/public-odi-audit.md).
