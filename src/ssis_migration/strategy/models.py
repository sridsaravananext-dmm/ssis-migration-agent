from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TargetLanguageDecision:
    target_platform: str
    language: str
    ai_required: bool
    confidence: str
    reasons: list[str] = field(default_factory=list)
    unsupported_features: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_platform": self.target_platform,
            "language": self.language,
            "ai_required": self.ai_required,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "unsupported_features": self.unsupported_features,
            "evidence": self.evidence,
        }