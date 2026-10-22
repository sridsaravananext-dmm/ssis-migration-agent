from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ssis_migration.agent.code_validator import validate_generated_code
from .context_builder import AIMigrationContextBuilder
from .models import AIMigrationResult
from .planner import AIMigrationPlanner
from .provider import AIProvider
from .prompt_builder import AIMigrationPromptBuilder
from .response_validator import (
    AIResponseValidationError,
    AIResponseValidator,
)


class AIMigrationOrchestrator:
    """
    Coordinates the deterministic AI migration preparation flow.

    MigrationStep
        -> Context Builder
        -> AI Planner
        -> AI Provider
        -> Response Validator
        -> AIMigrationResult
    """

    def __init__(
        self,
        provider: AIProvider,
    ) -> None:

        self.context_builder = AIMigrationContextBuilder()
        self.planner = AIMigrationPlanner()
        self.provider = provider
        self.response_validator = AIResponseValidator()
        self.prompt_builder = AIMigrationPromptBuilder()

    def run(
        self,
        migration_step: Any,
        procedure_definitions: dict[str, str] | None = None,
    ) -> AIMigrationResult:

        # --------------------------------------------------
        # Normalize MigrationStep
        # --------------------------------------------------

        if hasattr(migration_step, "to_dict"):
            migration_step_data = migration_step.to_dict()

        elif hasattr(
            migration_step,
            "__dataclass_fields__",
        ):
            migration_step_data = asdict(
                migration_step
            )

        elif isinstance(
            migration_step,
            dict,
        ):
            migration_step_data = migration_step

        else:
            raise TypeError(
                "migration_step must be a MigrationStep, "
                "dataclass, or dictionary"
            )

        # --------------------------------------------------
        # 1. Build deterministic migration context
        # --------------------------------------------------

        context = self.context_builder.build(
            migration_step_data,
            package_ir=getattr(
                migration_step,
                "package_ir",
                None,   
            ),
            databases=getattr(
                migration_step,
                "databases",
                None,
            ) or migration_step_data.get("databases"),
            procedure_definitions=procedure_definitions,
        )
        contract = context.get("migration_contract")
        if contract and contract["implementation_strategy"]["decision"] == "UNSUPPORTED":
            return AIMigrationResult(
                status="UNRESOLVED", language=migration_step_data.get("target_language", "pyspark"),
                code="", assumptions=contract["assumptions"],
                unresolved_items=contract["unresolved_items"],
                validation_requirements=contract["validation_requirements"],
                metadata={"migration_contract_id": contract["contract_id"], "validation_scope": "CONTRACT_STRUCTURE"},
            )

        # --------------------------------------------------
        # 2. Build structured AI request
        # --------------------------------------------------

        request = self.planner.build_request(
            context
        )

        # --------------------------------------------------
        # 3. Call AI provider
        # --------------------------------------------------

        prompt = self.prompt_builder.build(
            request
        )

        response = self.provider.generate(
            request,
            prompt,
        )

        # --------------------------------------------------
        # 4. Validate provider response
        # --------------------------------------------------

        try:
            response = self.response_validator.validate(
                response
            )

        except AIResponseValidationError as exc:

            return AIMigrationResult(
                status="UNRESOLVED",
                language=request["task"].get(
                    "target_language",
                    "unknown",
                ),
                code="",
                assumptions=[],
                unresolved_items=[
                    f"Invalid AI provider response: {exc}"
                ],
                validation_requirements=[
                    "Correct the provider response schema "
                    "before migration code can be accepted."
                ],
                metadata={
                    "provider_response_validation": "FAILED",
                    "provider": (
                        response["metadata"].get("provider")
                        if isinstance(response, dict) and isinstance(
                            response.get("metadata"), dict,
                        )
                        else None
                    ),
                },
            )

        
        # --------------------------------------------------
        # 5. Static validation of generated migration code
        # --------------------------------------------------

        code_validation = validate_generated_code(
            code=response["code"],
            step_name=migration_step_data.get("name", ""),
            language=response["language"],
        )

        def language_family(value: str) -> str:
            normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in {"python", "py", "pyspark"}:
                return "python"
            if normalized in {"sql", "spark_sql", "databricks_sql", "databricks"}:
                return "sql"
            return normalized

        requested_language = request["task"].get("target_language")
        if requested_language and language_family(requested_language) != language_family(response["language"]):
            code_validation["issues"].append({
                "code": "LANGUAGE_MISMATCH", "severity": "ERROR", "line": None,
                "message": f"Requested {requested_language!r}, but provider returned {response['language']!r}.",
            })
            code_validation["status"] = "REJECTED"

        # A model cannot erase missing prerequisites found before its call.
        context_unresolved = [
            *context.get("temporal_context", {}).get("unresolved_items", []),
            *((contract or {}).get("unresolved_items", [])),
        ]
        response = dict(response)
        response["unresolved_items"] = list(dict.fromkeys([
            *response["unresolved_items"], *context_unresolved,
        ]))
        if contract:
            response["validation_requirements"] = list(dict.fromkeys([
                *contract["validation_requirements"], *response["validation_requirements"],
            ]))
            response["assumptions"] = list(dict.fromkeys([
                *contract["assumptions"], *response["assumptions"],
            ]))

        metadata = dict(response.get("metadata") or {})
        metadata["static_code_validation"] = code_validation
        metadata["context_unresolved_items"] = list(context_unresolved)
        metadata["validation_scope"] = "STATIC_CODE_ONLY"
        if contract:
            metadata["migration_contract_id"] = contract["contract_id"]

        if code_validation["status"] == "REJECTED":
            issues = code_validation.get("issues", [])

            error_messages = [
                (
                    f"{issue['code']}: {issue['message']}"
                    + (
                        f" (line {issue['line']})"
                        if issue.get("line")
                        else ""
                    )
                )
                for issue in issues
                if issue.get("severity") == "ERROR"
            ]

            return AIMigrationResult(
                status="UNRESOLVED",
                language=response["language"],
                code=response["code"],
                assumptions=response["assumptions"],
                unresolved_items=[
                    *response["unresolved_items"],
                    "Static code validation rejected the generated code.",
                    *error_messages,
                ],
                validation_requirements=[
                    *response["validation_requirements"],
                    "Correct all static validation errors and regenerate "
                    "or revalidate the migration before acceptance.",
                    "Do not execute or publish rejected migration code.",
                ],
                metadata=metadata,
            )

        # A REVIEW_REQUIRED result from the AI remains review-required.
        # Static checks do not override the AI's unresolved items.
        final_status = response["status"]

        if response["unresolved_items"] and final_status == "GENERATED":
            final_status = "UNRESOLVED"

        if code_validation["status"] == "REVIEW_REQUIRED":
            if final_status not in {"UNRESOLVED", "REVIEW_REQUIRED"}:
                final_status = "REVIEW_REQUIRED"

        return AIMigrationResult(
            status=final_status,
            language=response["language"],
            code=response["code"],
            assumptions=response["assumptions"],
            unresolved_items=response["unresolved_items"],
            validation_requirements=response["validation_requirements"],
            metadata=metadata,
        )

