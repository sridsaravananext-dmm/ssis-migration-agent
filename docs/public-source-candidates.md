# Public source exports

Public exports can supplement user-supplied projects. Preserve original bytes,
licenses, upstream commit IDs and hashes. Public sample availability does not
establish complete dependencies or migration equivalence. Do not generate missing
source files, metadata, input rows or expected outputs.

| Platform | Existing public material | Current disposition |
| --- | --- | --- |
| SSIS | [Microsoft WideWorldImporters SSIS project](https://github.com/microsoft/sql-server-samples/tree/348bbbc25079785f93ea74be3dd0d6369a59358f/samples/databases/wide-world-importers/wwi-ssis), with adjacent SSDT database projects; MIT | Repository already has WWI regressions. Full project/dependency comparison with the pinned upstream version remains to be done. |
| PowerCenter | [Praveen-pa SCD1 mapping/workflow/log](https://github.com/Praveen-pa/Informatica-Powercenter/tree/43a58b9629b767454e5622913d12f53335866ea2/SCD1); MIT, original embedded vendor notices retained | Imported unchanged with [provenance](../examples/public/powercenter_scd1/provenance.json). Declared Windows-1252 replay and [explicit session/mapping binding](powercenter-dependency-binding.md) are implemented. Broader dependencies and transformation semantics remain unresolved. |
| ODI | [Oracle ETL Offload Smart Export](https://github.com/oracle/data-warehouse-etl-offload-samples/tree/4f67a1ccf84c6a4ef02720690cdee61af3027f25/CloudAtCustomerODIETLOffload), plus `SampleSourceFiles/` CSVs; UPL 1.0 | Original ZIP/license/documentation imported unchanged with [provenance](../examples/public/oracle_odi_etl_offload/provenance.json). [Versioned normalization](odi-mapping-normalization.md) now builds mapping declarations, explicit references and review contracts. Databricks capability reports identify missing definitions and unimplemented physical/KM semantics. CSVs have not been imported; target generation remains blocked. |
| TM1 | [TIdy](https://github.com/dpebert7/TIdy/tree/2a7817aa0630048a06ac3a4c8904f88613d51a6c); MIT | Native `.pro` format-test examples, including intentionally incomplete code; not a working cube/model or semantic baseline. |
| BODS | [BODS Data Integrity Checks](https://github.com/brudosm/BODS-Data-Integrity-Checks/tree/9b06c0d719749ba95e1810276b2d60a4b46ce482); MIT | Supporting custom functions and schema instructions, not a complete ATL job. A licensed native job with dependencies is still needed. |

[Bedrock](https://github.com/cubewise-code/bedrock/tree/81e10ba4210c728a006dfcee6d776bc235ecea53)
contains substantive TurboIntegrator processes, but its custom license restricts
online republication without express permission. It has not been copied into this
repository. Do not treat publicly readable repositories as automatically reusable.

Continue with one source capability at a time. The current
[PowerCenter audit](public-powercenter-audit.md) separates encoding compatibility,
structural preservation, multi-file binding and target semantics. The
[source checklist](source-export-request.md) still applies to missing project
dependencies and actual runtime observations.
