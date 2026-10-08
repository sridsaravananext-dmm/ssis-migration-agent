from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI

from .provider import AIProvider


AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "enum": [
                "GENERATED",
                "REVIEW_REQUIRED",
                "UNRESOLVED",
            ],
        },
        "language": {
            "type": "string",
        },
        "code": {
            "type": "string",
        },
        "assumptions": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "unresolved_items": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "validation_requirements": {
            "type": "array",
            "items": {
                "type": "string",
            },
        },
        "metadata": {
            "type": "object",
            "additionalProperties": True,
        },
    },
    "required": [
        "status",
        "language",
        "code",
        "assumptions",
        "unresolved_items",
        "validation_requirements",
        "metadata",
    ],
    "additionalProperties": False,
}


class OpenAIProvider(AIProvider):
    """
    Real OpenAI provider for SSIS-to-Databricks migration.

    Configuration is supplied through environment variables:

        OPENAI_API_KEY
        OPENAI_MODEL

    Example:

        export OPENAI_API_KEY="..."
        export OPENAI_MODEL="..."
    """

    def __init__(
        self,
        *,
        client: OpenAI | None = None,
        model: str | None = None,
    ) -> None:

        self.client = client or OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY")
        )

        self.model = model or os.environ.get("OPENAI_MODEL")

        if not self.model:
            raise ValueError(
                "OPENAI_MODEL environment variable is required."
            )

    def generate(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:

        response = self.client.responses.create(
            model=self.model,
            input=prompt,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "ssis_migration_result",
                    "strict": True,
                    "schema": AI_RESPONSE_SCHEMA,
                }
            },
        )

        output_text = response.output_text

        if not output_text:
            raise RuntimeError(
                "OpenAI returned an empty response."
            )

        try:
            result = json.loads(output_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "OpenAI returned invalid JSON despite structured output."
            ) from exc

        result.setdefault("metadata", {})

        result["metadata"].update(
            {
                "provider": "openai",
                "model": self.model,
                "response_id": response.id,
                "prompt_length": len(prompt),
            }
        )

        return result