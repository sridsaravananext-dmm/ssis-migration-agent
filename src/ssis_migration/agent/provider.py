from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """
    Provider-independent interface for AI migration generation.

    The migration system builds the structured request and prompt.
    Provider implementations are responsible only for sending the
    prompt to an AI system and returning its structured response.
    """

    @abstractmethod
    def generate(
        self,
        request: dict[str, Any],
        prompt: str,
    ) -> dict[str, Any]:
        """
        Generate a migration response from an AI provider.

        Parameters
        ----------
        request:
            Structured migration request produced by AIMigrationPlanner.

        prompt:
            Final prompt produced by AIMigrationPromptBuilder.

        Returns
        -------
        dict[str, Any]
            Provider response that will subsequently be validated by
            AIResponseValidator.
        """
        raise NotImplementedError