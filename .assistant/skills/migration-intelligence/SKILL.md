---
name: migration-intelligence
description: Analyze and implement one logical data migration step from normalized migration context. Use when translating source semantics, workflow behavior, data-flow mappings, destination behavior, incremental logic, lineage, or migration strategy into a target implementation.
---

# Migration Intelligence

## Purpose

This skill provides generic migration reasoning for one logical data
migration step.

The skill is platform-independent.

Do not assume the source is SSIS, TM1, T-SQL, PostgreSQL, or any
specific ETL technology.

Do not assume the destination is Databricks, Snowflake, Azure
Synapse, or any other specific target platform.

The supplied migration context is the source of truth.

---

# 1. Migration Context

A migration request may contain the following logical sections:

- migration
- strategy
- source
- workflow
- destination
- requirements
- evidence

Treat these as structured migration evidence.

The exact fields may vary by source and target adapter.

Do not invent missing information.

---

# 2. Migration Objective

The objective is:

Source behavior
    ->
preserved migration semantics
    ->
target implementation

This is a migration task, not a redesign task.

Preserve behavior before attempting optimization or modernization.

If an optimization changes behavior, do not perform it silently.

---

# 3. Source Analysis

Inspect the source context for:

- source platform
- source objects
- source procedures
- source definitions
- source semantics
- extraction patterns
- filtering behavior
- joins
- unions
- aggregations
- temporary objects
- loops
- cursors
- temporal logic
- point-in-time lookups
- incremental logic
- cutoff or watermark behavior

Use only information actually represented in the supplied context.

If source behavior is ambiguous, record the ambiguity.

---

# 4. Workflow Analysis

Inspect workflow context for:

- load type
- control-flow operations
- data flows
- transformations
- dependencies
- execution ordering
- staging behavior
- lookup behavior
- key resolution
- lineage behavior
- cutoff or watermark handling

Preserve meaningful execution dependencies.

Do not remove an operation merely because it appears unnecessary
from the target platform's perspective.

First determine what semantic behavior the operation represents.

---

# 5. Data-Flow Analysis

For each data flow identify:

1. source components
2. transformations
3. destination components
4. column mappings
5. dependencies
6. filtering
7. joins
8. aggregations
9. lookups
10. derived values

Preserve mappings unless the supplied context explicitly indicates
that they are no longer required.

Do not silently drop columns.

Do not silently rename columns.

Do not silently change data types.

Do not silently change null behavior.

---

# 6. Destination Analysis

Inspect destination context for:

- target objects
- destination procedures
- target semantics
- key resolution
- inserts
- updates
- merges
- lineage completion
- cutoff updates
- target-side validation
- target-specific loading behavior

Destination behavior is part of the migration semantics.

Do not assume that loading the final target table is sufficient.

If the source workflow performs staging, key resolution, lineage,
or cutoff management, determine how that behavior is represented
in the target implementation.

---

# 7. Incremental Processing

Incremental behavior is a critical migration concern.

Look for evidence such as:

- last processed timestamp
- cutoff timestamp
- watermark
- change tracking
- change data capture
- temporal filtering
- modified-date filtering
- high-water mark
- incremental extraction procedure
- incremental load procedure

Preserve the semantic meaning of incremental processing.

Do not replace incremental processing with a full refresh unless
the supplied migration requirements explicitly permit it.

If the incremental behavior cannot be translated safely, report it
as an unresolved item.

---

# 8. Lineage

If the supplied migration context contains lineage behavior:

- preserve lineage generation
- preserve lineage identifiers
- preserve lineage relationships
- preserve lineage completion behavior

Do not remove lineage logic simply because the target platform
provides a different lineage mechanism.

First determine whether the existing lineage represents:

- technical lineage
- ETL execution lineage
- batch lineage
- audit lineage
- source-to-target traceability

If the distinction cannot be established from the supplied context,
report an assumption or unresolved item.

---

# 9. Strategy

The migration strategy may identify the migration as:

- deterministic
- AI-assisted
- review-required

Use the supplied strategy as guidance.

Do not override a deterministic strategy merely because another
implementation appears more convenient.

If the strategy indicates AI assistance is required, analyze the
complexity signals before generating code.

Examples include:

- cursor
- loop
- temporary tables
- temporal logic
- point-in-time lookup
- composite incremental logic
- complex relational logic

---

# 10. Evidence Hierarchy

When interpreting migration behavior, prefer evidence in this order:

1. Explicit migration requirements
2. Destination semantics
3. Source semantics
4. Workflow behavior
5. Data-flow mappings
6. Migration strategy
7. Database metadata
8. Reasonable implementation assumptions

Never treat an unsupported assumption as established behavior.

---

# 11. Assumptions

Every assumption that affects implementation must be explicit.

Examples:

- target table already exists
- target schema is available
- a source type maps to a target type
- a platform-specific operation has an equivalent target operation
- an external dependency is available

Do not hide assumptions inside generated code.

---

# 12. Unresolved Items

Report unresolved behavior when:

- source semantics are incomplete
- destination semantics are incomplete
- a transformation has no known equivalent
- a dependency cannot be resolved
- data types cannot be safely mapped
- incremental behavior is ambiguous
- lookup behavior is ambiguous
- business logic cannot be inferred safely

An unresolved item should describe:

1. what is unknown
2. why it matters
3. what information is required to resolve it

---

# 13. Code Generation Rules

When generating target code:

1. Generate executable code.
2. Preserve source semantics.
3. Preserve workflow semantics.
4. Preserve data-flow mappings.
5. Preserve destination semantics.
6. Preserve incremental behavior.
7. Preserve lineage behavior where represented.
8. Preserve cutoff or watermark behavior where represented.
9. Preserve lookup and key-resolution behavior.
10. Do not silently simplify complex SQL or transformation logic.
11. Do not invent business rules.
12. Make assumptions explicit.
13. Report unresolved behavior explicitly.
14. Include validation requirements.

---

# 14. Validation

Generated migration code must be validated against the supplied
migration context.

At minimum verify:

- source objects are represented
- destination objects are represented
- transformations are represented
- mappings are preserved
- dependencies are preserved
- incremental behavior is preserved
- lookup/key behavior is preserved
- lineage behavior is preserved where applicable
- cutoff/watermark behavior is preserved where applicable
- target operations are represented
- no unsupported business logic was introduced

Prefer semantic validation over textual similarity.

---

# 15. Important Rule

Never claim that migration behavior has been preserved merely
because the generated code runs successfully.

Successful execution proves execution.

Semantic validation determines whether migration behavior was
preserved.