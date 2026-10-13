from __future__ import annotations

import json
import os
from typing import Any

from .provider import AIProvider


# Default endpoint for the Databricks SDK path.
_DEFAULT_ENDPOINT = "databricks-gpt-oss-120b"
_DEFAULT_MODEL = "system.ai.gpt-oss-120b"

_SYSTEM_PROMPT = (
    "You generate migration artifacts. "
    "Follow the supplied prompt and return only "
    "the requested JSON object."
)


class DatabricksProvider(AIProvider):
    """
    AI provider backed by Databricks Foundation Model APIs.

    Supports two backends:

    1. **Databricks SDK** (default): Uses the WorkspaceClient
       runtime session for authentication. No PAT or openai
       package required. Works inside Databricks compute.

    2. **OpenAI-compatible** (fallback): Uses the openai Python
       package with DATABRICKS_TOKEN and DATABRICKS_BASE_URL
       environment variables. Requires the openai package.
    """

    def __init__(
        self,
        *,
        client: Any | None = None,
        model: str | None = None,
        endpoint_name: str | None = None,
        use_sdk: bool | None = None,
    ) -> None:
        self.model = model or os.environ.get(
            "DATABRICKS_MODEL", _DEFAULT_MODEL
        )

        self._sdk_client = None
        self._openai_client = client

        if client is not None:
            # Caller provided an explicit client (OpenAI or
            # compatible). Use the OpenAI path.
            self._backend = "openai"
        elif use_sdk is False:
            # Explicitly requested OpenAI path.
            self._backend = "openai"
            self._init_openai_client()
        else:
            # Try the Databricks SDK first (no PAT needed).
            try:
                from databricks.sdk import WorkspaceClient
                self._sdk_client = WorkspaceClient()
                self._backend = "sdk"
            except Exception:
                # Fall back to OpenAI client.
                self._backend = "openai"
                self._init_openai_client()

        # Resolve the serving endpoint name for the SDK path.
        self.endpoint_name = endpoint_name or os.environ.get(
            "DATABRICKS_ENDPOINT_NAME", _DEFAULT_ENDPOINT
        )

    def _init_openai_client(self) -> None:
        """Initialize the OpenAI-compatible client."""
        from openai import OpenAI

        token = os.environ.get("DATABRICKS_TOKEN")
        base_url = os.environ.get("DATABRICKS_BASE_URL")

        if not token:
            raise ValueError(
                "DATABRICKS_TOKEN environment variable is "
                "required when the Databricks SDK is not "
                "available."
            )

        if not base_url:
            raise ValueError(
                "DATABRICKS_BASE_URL environment variable is "
                "required when the Databricks SDK is not "
                "available."
            )

        self._openai_client = OpenAI(
            api_key=token,
            base_url=base_url,
        )

    def generate(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        if self._backend == "sdk":
            return self._generate_sdk(request, prompt)
        return self._generate_openai(request, prompt)

    # ---------------------------------------------------------
    # Databricks SDK backend
    # ---------------------------------------------------------

    def _generate_sdk(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        from databricks.sdk.service.serving import (
            ChatMessage,
            ChatMessageRole,
        )

        response = self._sdk_client.serving_endpoints.query(
            name=self.endpoint_name,
            messages=[
                ChatMessage(
                    role=ChatMessageRole.SYSTEM,
                    content=_SYSTEM_PROMPT,
                ),
                ChatMessage(
                    role=ChatMessageRole.USER,
                    content=prompt,
                ),
            ],
            max_tokens=16384,
            temperature=0.0,
        )

        if not response.choices:
            raise RuntimeError(
                "Databricks returned no completion choices."
            )

        content = response.choices[0].message.content

        # The SDK returns content as a list of blocks.
        # Extract only text blocks (skip reasoning blocks).
        if isinstance(content, list):
            text_parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "text"
            ]
            output_text = "\n".join(text_parts)
        elif isinstance(content, str):
            output_text = content
        else:
            output_text = str(content) if content else ""

        if not output_text.strip():
            raise RuntimeError(
                "Databricks returned an empty response."
            )

        result = self._parse_json(output_text)

        result.setdefault("metadata", {})
        result["metadata"].update(
            {
                "provider": "databricks",
                "model": self.model,
                "endpoint": self.endpoint_name,
                "response_id": getattr(
                    response, "id", None
                ),
                "prompt_length": len(prompt),
                "backend": "sdk",
            }
        )

        return result

    # ---------------------------------------------------------
    # OpenAI-compatible backend (existing)
    # ---------------------------------------------------------

    def _generate_openai(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        from .openai_provider import AI_RESPONSE_SCHEMA

        response = self._openai_client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": _SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "ssis_migration_result",
                    "strict": True,
                    "schema": AI_RESPONSE_SCHEMA,
                },
            },
            max_tokens=4096,
        )

        if not response.choices:
            raise RuntimeError(
                "Databricks returned no completion choices."
            )

        content = response.choices[0].message.content

        if isinstance(content, list):
            text_parts = [
                item.get("text", "")
                for item in content
                if isinstance(item, dict)
                and item.get("type") == "text"
            ]
            output_text = "\n".join(text_parts)
        else:
            output_text = content or ""

        if not output_text.strip():
            raise RuntimeError(
                "Databricks returned an empty response."
            )

        result = self._parse_json(output_text)

        result.setdefault("metadata", {})
        result["metadata"].update(
            {
                "provider": "databricks",
                "model": self.model,
                "response_id": response.id,
                "prompt_length": len(prompt),
                "backend": "openai",
            }
        )

        return result

    # ---------------------------------------------------------
    # Shared helpers
    # ---------------------------------------------------------

    @staticmethod
    def _parse_json(output_text: str) -> dict[str, Any]:
        """Parse JSON from the model's text output.

        Handles three response formats:
        1. Plain JSON.
        2. JSON wrapped in markdown code fences
           (```json ... ``` or ``` ... ```).
        3. JSON embedded in surrounding text
           (extract first {...} block).
        """
        text = output_text.strip()

        # 1. Try plain JSON.
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        # 2. Try extracting from markdown code fences.
        import re
        fence_pattern = re.compile(
            r"```(?:json)?\s*\n?(.*?)\n?```",
            re.DOTALL,
        )
        for match in fence_pattern.finditer(text):
            inner = match.group(1).strip()
            try:
                result = json.loads(inner)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                continue

        # 3. Try extracting the first {...} block.
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            try:
                result = json.loads(candidate)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

        raise RuntimeError(
            "Databricks returned invalid JSON."
        )
