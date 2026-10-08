from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .provider import AIProvider


class PuterAIProvider(AIProvider):
    """
    AI provider backed by the Puter.js bridge.

    Authentication is handled by the Puter CLI/session through the
    JavaScript bridge. No PUTER_AUTH_TOKEN is required here.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        bridge_path: str | Path = "scripts/puter_bridge.mjs",
    ) -> None:
        self.model = (
            model
            or os.environ.get("PUTER_MODEL")
            or "gpt-5.6-luna"
        )
        self.bridge_path = Path(bridge_path)

    def generate(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:

        if not self.bridge_path.exists():
            raise FileNotFoundError(
                f"Puter bridge not found: {self.bridge_path}"
            )

        environment = os.environ.copy()
        environment["PUTER_MODEL"] = self.model

        process = subprocess.run(
            ["node", str(self.bridge_path)],
            input=prompt,
            text=True,
            capture_output=True,
            env=environment,
            check=False,
        )

        if process.returncode != 0:
            error = process.stderr.strip()

            raise RuntimeError(
                error or "Puter AI provider failed."
            )

        output = process.stdout.strip()

        if not output:
            raise RuntimeError(
                "Puter AI provider returned an empty response."
            )

        try:
            response = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Puter AI provider returned invalid JSON."
            ) from exc

        if not isinstance(response, dict):
            raise RuntimeError(
                "Puter AI provider returned a non-object response."
            )

        response.setdefault("metadata", {})
        response["metadata"].update(
            {
                "provider": "puter",
                "model": self.model,
            }
        )

        return response