from __future__ import annotations

from typing import Any


ALLOWED_STATUSES = {
    "GENERATED",
    "REVIEW_REQUIRED",
    "UNRESOLVED",
    # Internal test/provider status.
    "MOCK_RESPONSE",
}

REQUIRED_FIELDS = {
    "status",
    "language",
    "code",
    "assumptions",
    "unresolved_items",
    "validation_requirements",
    "metadata",
}


class AIResponseValidationError(ValueError):
    """Raised when an AI provider response violates the migration contract."""


class AIResponseValidator:
    """
    Deterministically validates the structured response returned by
    an AI migration provider.
    """

    def validate(
        self,
        response: Any,
    ) -> dict[str, Any]:

        if not isinstance(response, dict):
            raise AIResponseValidationError(
                "AI provider response must be a dictionary."
            )

        missing = REQUIRED_FIELDS - response.keys()

        if missing:
            raise AIResponseValidationError(
                "AI provider response is missing required fields: "
                + ", ".join(sorted(missing))
            )

        status = response["status"]

        if not isinstance(status, str):
            raise AIResponseValidationError(
                "Field 'status' must be a string."
            )

        if status not in ALLOWED_STATUSES:
            raise AIResponseValidationError(
                f"Unsupported migration status: {status!r}"
            )

        language = response["language"]

        if not isinstance(language, str) or not language.strip():
            raise AIResponseValidationError(
                "Field 'language' must be a non-empty string."
            )

        code = response["code"]

        if not isinstance(code, str):
            raise AIResponseValidationError(
                "Field 'code' must be a string."
            )

        for field_name in (
            "assumptions",
            "unresolved_items",
            "validation_requirements",
        ):
            value = response[field_name]

            if not isinstance(value, list):
                raise AIResponseValidationError(
                    f"Field '{field_name}' must be a list."
                )

            if not all(
                isinstance(item, str)
                for item in value
            ):
                raise AIResponseValidationError(
                    f"All values in '{field_name}' must be strings."
                )

        metadata = response["metadata"]

        if not isinstance(metadata, dict):
            raise AIResponseValidationError(
                "Field 'metadata' must be a dictionary."
            )

        # A generated migration must contain executable code.
        if status == "GENERATED" and not code.strip():
            raise AIResponseValidationError(
                "A GENERATED migration response must contain code."
            )

        return response
