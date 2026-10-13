"""
Code validator for generated migration artifacts.

Validates that generated code is structurally sound:
  - SQL artifacts are parseable (using sqlglot).
  - PySpark artifacts have no syntax errors (using ast).
  - Artifacts are not empty.
  - Artifacts have recognized languages and types.
  - AI contracts have the AI_REQUIRED marker.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GeneratedCodeValidation:
    status: str
    checks: dict[str, bool] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checks": dict(self.checks),
            "failures": list(self.failures),
        }


class GeneratedCodeValidator:
    """Validate generated code artifacts."""

    VALID_LANGUAGES = {"spark_sql", "pyspark"}
    VALID_ARTIFACT_TYPES = {
        "AI_MIGRATION_ARTIFACT",
        "NOTEBOOK_CELL",
        "SQL_TRANSLATION",
        "AI_CONTRACT",
        "UNRESOLVED",
    }

    def validate_artifact(
        self, artifact: dict[str, Any]
    ) -> GeneratedCodeValidation:
        """
        Validate a single generated artifact.

        Args:
            artifact: A GeneratedArtifact dict
                (from GeneratedArtifact.to_dict()).
        """
        checks: dict[str, bool] = {}
        failures: list[str] = []

        code = artifact.get("code", "")
        language = artifact.get("language", "")
        artifact_type = artifact.get("artifact_type", "")
        artifact_id = artifact.get(
            "artifact_id", "unknown"
        )

        # 1. Non-empty code
        checks["code_not_empty"] = bool(
            code and code.strip()
        )
        if not checks["code_not_empty"]:
            failures.append(
                f"Artifact {artifact_id}: code is empty."
            )

        # 2. Language is recognized
        checks["language_recognized"] = (
            language in self.VALID_LANGUAGES
        )
        if not checks["language_recognized"]:
            failures.append(
                f"Artifact {artifact_id}: unrecognized "
                f"language '{language}'."
            )

        # 3. Syntax validation
        if code and code.strip():
            if language == "spark_sql":
                try:
                    import sqlglot
                    sqlglot.parse_one(
                        code, read="databricks"
                    )
                    checks["sql_parseable"] = True
                except Exception as exc:
                    checks["sql_parseable"] = False
                    failures.append(
                        f"Artifact {artifact_id}: "
                        f"SQL parse error: "
                        f"{type(exc).__name__}: {exc}"
                    )
            elif language == "pyspark":
                try:
                    ast.parse(code)
                    checks["python_parseable"] = True
                except (SyntaxError, ValueError) as exc:
                    checks["python_parseable"] = False
                    failures.append(
                        f"Artifact {artifact_id}: "
                        f"Python syntax error: "
                        f"{type(exc).__name__}: {exc}"
                    )

        # 4. Artifact type is recognized
        checks["artifact_type_recognized"] = (
            artifact_type in self.VALID_ARTIFACT_TYPES
        )
        if not checks["artifact_type_recognized"]:
            failures.append(
                f"Artifact {artifact_id}: "
                f"unrecognized type '{artifact_type}'."
            )

        # 5. AI contracts should have AI_REQUIRED marker
        if (
            language == "pyspark"
            and artifact_type in {
                "AI_CONTRACT",
                "AI_MIGRATION_ARTIFACT",
            }
        ):
            checks["ai_required_marker"] = (
                "# AI_REQUIRED" in code
            )
            if not checks["ai_required_marker"]:
                failures.append(
                    f"Artifact {artifact_id}: "
                    "AI contract is missing "
                    "'# AI_REQUIRED' marker."
                )

        status = "PASS" if not failures else "FAIL"

        return GeneratedCodeValidation(
            status=status,
            checks=checks,
            failures=failures,
        )

    def validate(
        self,
        artifacts: list[dict[str, Any]],
    ) -> list[GeneratedCodeValidation]:
        """
        Validate a list of generated artifacts.

        Args:
            artifacts: List of GeneratedArtifact dicts.

        Returns:
            List of validation results, one per artifact.
        """
        return [
            self.validate_artifact(a) for a in artifacts
        ]