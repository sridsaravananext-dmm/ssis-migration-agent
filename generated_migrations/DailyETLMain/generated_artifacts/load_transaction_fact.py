# AI_REQUIRED
#
# ============================================================
# SSIS -> Databricks AI Migration Artifact
# ============================================================
#
# Migration step:
# Load Transaction Fact
#
# Target:
# Databricks PySpark
#
# Migration strategy:
# AI_ASSISTED
#
# Migration patterns:
# ['INCREMENTAL_COMPOSITE']
#
# Complexity signals:
# ['UNION']
#
# ------------------------------------------------------------
# SOURCE PROCEDURE
# ------------------------------------------------------------
#
# Integration.GetTransactionUpdates
#
# The complete source procedure is preserved in artifact
# metadata under:
#
#   source_procedure_definition
#
# The AI migration agent must preserve its semantics.
#
# ------------------------------------------------------------
# DESTINATION PROCEDURE
# ------------------------------------------------------------
#
# Integration.MigrateStagedTransactionData
#
# The complete destination procedure is preserved in
# artifact metadata under:
#
#   destination_procedure_definition
#
# The AI migration agent must analyze its target-side
# semantics before generating the Databricks implementation.
#
# ------------------------------------------------------------
# MIGRATION REQUIREMENT
# ------------------------------------------------------------
#
# Generate executable Databricks PySpark logic equivalent to
# the original SSIS workflow.
#
# The implementation must account for:
#
# 1. Source extraction semantics
# 2. Incremental cutoff handling
# 3. SSIS data-flow mappings
# 4. Staging behavior
# 5. Destination transformation semantics
# 6. Dimension/surrogate-key lookups
# 7. Target fact/dimension loading
# 8. Lineage behavior
# 9. ETL cutoff behavior
# 10. Transaction / consistency semantics
#
# Do not invent business logic that is not supported by the
# supplied source SQL, destination SQL, SSIS metadata, or
# database metadata.
#
# This artifact is an AI input contract, not executable code.
