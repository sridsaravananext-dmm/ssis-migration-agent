
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# ============================================================
# Resolution result
# ============================================================

@dataclass(frozen=True)
class ResolutionResult:
    migration_strategy: str
    generation_status: str
    reasons: tuple[str, ...]
    unresolved_items: tuple[str, ...]
    required_capabilities: tuple[str, ...]
    source_contract_version: str = "1.0"


# ============================================================
# Deterministic migration resolver
# ============================================================

class DeterministicMigrationResolver:
    """
    Resolves a migration contract into a generation strategy.

    This class does not:
      - Execute SQL or Spark code.
      - Modify source or destination data.
      - Guess unverified business behavior.
      - Use an LLM to make migration decisions.

    It only decides what the generator can safely attempt.
    """

    def resolve(
        self,
        contract: dict[str, Any],
    ) -> ResolutionResult:

        if not isinstance(contract, dict):
            raise TypeError(
                "Migration contract must be a dictionary."
            )

        if not contract:
            raise ValueError(
                "Migration contract is empty."
            )

        reasons: list[str] = []
        unresolved: list[str] = []
        capabilities: list[str] = []

        # ----------------------------------------------------
        # 1. Read the contract's known semantic information
        # ----------------------------------------------------

        intent = contract.get("migration_intent") or {}
        if not isinstance(intent, dict):
            unresolved.append(
                "migration_intent has an invalid structure"
            )
            intent = {}

        features = self._collect_features(contract, intent)

        # ----------------------------------------------------
        # 2. Identify unresolved migration semantics
        # ----------------------------------------------------

        unresolved.extend(
            self._collect_unresolved_items(contract)
        )

        # ----------------------------------------------------
        # 3. Determine required execution capabilities
        # ----------------------------------------------------

        if features & {
            "JOIN",
            "UNION",
            "FILTER",
            "AGGREGATION",
            "SORT",
        }:
            capabilities.append("spark_sql")

        if features & {
            "CUSTOM_LOGIC",
            "FUZZY_LOOKUP",
            "ITERATION",
            "COMPLEX_CONDITIONAL",
        }:
            capabilities.append("pyspark")

        if features & {
            "SCD_TYPE_2",
            "SCD2",
            "INCREMENTAL_LOAD",
            "CHANGE_DATA_CAPTURE",
        }:
            capabilities.append("stateful_or_incremental_processing")

        # ----------------------------------------------------
        # 4. Resolve strategy deterministically
        # ----------------------------------------------------

        if unresolved:
            strategy = "manual_review"
            status = "blocked"

            reasons.append(
                "Required migration semantics are unresolved."
            )

        elif (
            "spark_sql" in capabilities
            and "pyspark" in capabilities
        ):
            strategy = "hybrid"
            status = "ready_for_generation"

            reasons.append(
                "Contract requires both SQL-oriented and "
                "programmatic transformations."
            )

        elif "pyspark" in capabilities:
            strategy = "pyspark"
            status = "ready_for_generation"

            reasons.append(
                "Contract requires programmatic transformation logic."
            )

        else:
            strategy = "spark_sql"
            status = "ready_for_generation"

            reasons.append(
                "Contract contains no identified requirement "
                "for programmatic transformations."
            )

        # ----------------------------------------------------
        # 5. Preserve stable ordering and remove duplicates
        # ----------------------------------------------------

        return ResolutionResult(
            migration_strategy=strategy,
            generation_status=status,
            reasons=tuple(dict.fromkeys(reasons)),
            unresolved_items=tuple(dict.fromkeys(unresolved)),
            required_capabilities=tuple(
                dict.fromkeys(capabilities)
            ),
        )

    # ========================================================
    # Helpers
    # ========================================================

    @staticmethod
    def _collect_features(
        contract: dict[str, Any],
        intent: dict[str, Any],
    ) -> set[str]:

        features: set[str] = set()

        # Read features from either location, if present.
        # We will align these keys to your actual contract schema.

        candidates = [
            contract.get("relational_features", []),
            contract.get("operations", []),
            intent.get("relational_features", []),
            intent.get("operations", []),
            intent.get("required_capabilities", []),
        ]

        for candidate in candidates:
            if isinstance(candidate, str):
                features.add(candidate.upper())
            elif isinstance(candidate, (list, tuple, set)):
                for item in candidate:
                    if isinstance(item, str):
                        features.add(item.upper())

        # Normalize common feature names.
        aliases = {
            "GROUP BY": "AGGREGATION",
            "GROUP_BY": "AGGREGATION",
            "UNION ALL": "UNION",
            "SCD TYPE 2": "SCD_TYPE_2",
            "SCD2": "SCD2",
            "LOOP": "ITERATION",
            "FOR EACH": "ITERATION",
            "FOR_EACH": "ITERATION",
            "FUZZY LOOKUP": "FUZZY_LOOKUP",
            "FUZZYLOOKUP": "FUZZY_LOOKUP",
            "CDC": "CHANGE_DATA_CAPTURE",
            "CUSTOM CODE": "CUSTOM_LOGIC",
        }

        return {
            aliases.get(feature, feature)
            for feature in features
        }

    @staticmethod
    def _collect_unresolved_items(
        contract: dict[str, Any],
    ) -> list[str]:

        unresolved: list[str] = []

        # Explicit unresolved items supplied by the analyzer.
        for key in (
            "unresolved_items",
            "unresolved_semantics",
            "open_questions",
        ):
            value = contract.get(key, [])

            if isinstance(value, str):
                unresolved.append(value)

            elif isinstance(value, (list, tuple, set)):
                unresolved.extend(
                    item for item in value
                    if isinstance(item, str)
                )

        # An explicit false/unknown verification flag means
        # the corresponding behavior cannot be assumed.
        verification = contract.get("verification", {})

        if isinstance(verification, dict):
            for name, state in verification.items():
                if state is False or (
                    isinstance(state, str)
                    and state.lower() in {
                        "unknown",
                        "unverified",
                        "unresolved",
                    }
                ):
                    unresolved.append(
                        f"Unverified behavior: {name}"
                    )

        return list(dict.fromkeys(unresolved))


# ============================================================
# Serialization helper
# ============================================================

def resolve_migration_contract(
    contract: dict[str, Any],
) -> dict[str, Any]:
    """
    Convenience function for integration with the analyzer.
    Returns a JSON-serializable dictionary.
    """

    result = DeterministicMigrationResolver().resolve(contract)
    return asdict(result)
