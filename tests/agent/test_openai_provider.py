from __future__ import annotations

import json

from ssis_migration.agent.openai_provider import (
    OpenAIProvider,
)


class FakeResponse:
    id = "resp_test_123"

    output_text = json.dumps(
        {
            "status": "GENERATED",
            "language": "pyspark",
            "code": "print('migration')",
            "assumptions": [],
            "unresolved_items": [],
            "validation_requirements": [
                "Compare source and target row counts."
            ],
            "metadata": {},
        }
    )


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.responses = FakeResponses()


def test_openai_provider_contract():

    client = FakeClient()

    provider = OpenAIProvider(
        client=client,
        model="test-model",
    )

    request = {
        "task": {
            "step_id": "load_transaction_fact",
            "target_language": "pyspark",
        }
    }

    prompt = "migration prompt"

    result = provider.generate(
        request,
        prompt,
    )

    assert result["status"] == "GENERATED"
    assert result["language"] == "pyspark"
    assert result["code"] == "print('migration')"

    assert result["metadata"]["provider"] == "openai"
    assert result["metadata"]["model"] == "test-model"
    assert result["metadata"]["response_id"] == "resp_test_123"
    assert result["metadata"]["prompt_length"] == len(prompt)

    assert len(client.responses.calls) == 1

    call = client.responses.calls[0]

    assert call["model"] == "test-model"
    assert call["input"] == prompt

    assert call["text"]["format"]["type"] == "json_schema"
    assert call["text"]["format"]["strict"] is True