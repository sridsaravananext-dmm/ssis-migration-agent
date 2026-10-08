from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .models import GeneratedArtifact


class MigrationGenerator(ABC):

    @abstractmethod
    def generate(
        self,
        migration_step: dict[str, Any],
        target_language: str,
    ) -> GeneratedArtifact:
        """Generate a target artifact from one migration step."""
        raise NotImplementedError
