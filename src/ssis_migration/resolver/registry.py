from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ssis_migration.resolver.base import Resolver


@dataclass
class ResolverSelection:
    status: str
    resolver: Resolver | None = None
    candidates: list[str] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "resolver": (
                self.resolver.name
                if self.resolver is not None
                else None
            ),
            "candidates": list(self.candidates or []),
            "error": self.error,
        }


class ResolverRegistry:
    """
    Registry of migration resolvers.

    The registry discovers and selects resolvers.
    It does not perform migration resolution.
    """

    def __init__(
        self,
        resolvers: list[Resolver] | None = None,
    ) -> None:
        self.resolvers = resolvers or []

    def register(
        self,
        resolver: Resolver,
    ) -> None:
        self.resolvers.append(resolver)

    def find_candidates(
        self,
        context: dict[str, Any],
    ) -> list[Resolver]:
        return [
            resolver
            for resolver in self.resolvers
            if resolver.can_handle(context)
        ]

    def select(
        self,
        context: dict[str, Any],
    ) -> ResolverSelection:
        candidates = self.find_candidates(context)

        candidate_names = [
            resolver.name
            for resolver in candidates
        ]

        if not candidates:
            return ResolverSelection(
                status="UNRESOLVED",
                candidates=[],
                error="No resolver can handle this migration context.",
            )

        if len(candidates) > 1:
            return ResolverSelection(
                status="AMBIGUOUS",
                candidates=candidate_names,
                error=(
                    "Multiple resolvers can handle this "
                    "migration context."
                ),
            )

        return ResolverSelection(
            status="SELECTED",
            resolver=candidates[0],
            candidates=candidate_names,
        )

    def resolve(
        self,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        selection = self.select(context)

        if selection.status == "UNRESOLVED":
            return selection.to_dict()

        if selection.status == "AMBIGUOUS":
            return selection.to_dict()

        if selection.resolver is None:
            return {
                "status": "UNRESOLVED",
                "resolver": None,
                "candidates": selection.candidates or [],
                "error": (
                    "Resolver selection reported SELECTED "
                    "without a resolver."
                ),
            }

        result = selection.resolver.resolve(context)

        if not isinstance(result, dict):
            return {
                "status": "UNRESOLVED",
                "resolver": selection.resolver.name,
                "candidates": selection.candidates or [],
                "error": (
                    "Resolver returned an invalid result type: "
                    f"{type(result).__name__}"
                ),
            }

        return result