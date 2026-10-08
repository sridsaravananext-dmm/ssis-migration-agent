from __future__ import annotations

from typing import Any

from .provider import AIProvider


class MockAIProvider(AIProvider):

    def generate(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:

        return {
            "status": "MOCK_RESPONSE",
            "language": request["task"].get(
                "target_language",
                "pyspark",
            ),
            "code": (
                "# MOCK AI RESPONSE\n"
                "# No real LLM has been called.\n"
            ),
            "assumptions": [],
            "unresolved_items": [
                "Mock provider does not generate migration code."
            ],
            "validation_requirements": [],
            "metadata": {
                "provider": "mock",
                "step_id": request["task"].get(
                    "step_id"
                ),
                "prompt_length": len(prompt),
            },
        }