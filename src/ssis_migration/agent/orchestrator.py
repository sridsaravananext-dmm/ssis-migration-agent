from __future__ import annotations

from dataclasses import asdict
from typing import Any

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
            ),
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
                        response.get("metadata", {}).get(
                            "provider"
                        )
                        if isinstance(response, dict)
                        else None
                    ),
                },
            )

        # --------------------------------------------------
        # 5. Convert validated response
        # --------------------------------------------------

        return AIMigrationResult(
            status=response["status"],
            language=response["language"],
            code=response["code"],
            assumptions=response["assumptions"],
            unresolved_items=response[
                "unresolved_items"
            ],
            validation_requirements=response[
                "validation_requirements"
            ],
            metadata=response["metadata"],
        )
