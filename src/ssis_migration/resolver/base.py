from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Resolver(ABC):
    """
    Generic resolver contract.

    A resolver determines whether it can handle a migration
    resolution request and, if so, produces a resolution result.

    This abstraction is intentionally platform-independent.
    """

    name: str

    @abstractmethod
    def can_handle(
        self,
        context: dict[str, Any],
    ) -> bool:
        """
        Return True when this resolver can safely attempt
        the supplied resolution context.
        """
        raise NotImplementedError

    @abstractmethod
    def resolve(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Resolve the supplied migration context.

        The result must be structured and must explicitly
        report its status.
        """
        raise NotImplementedError