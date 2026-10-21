from .databricks_provider import DatabricksProvider
from .mock_provider import MockAIProvider
from .provider import AIProvider


def create_provider(name: str) -> AIProvider:
    normalized = name.strip().lower()

    if normalized == "databricks":
        return DatabricksProvider()

    if normalized == "mock":
        return MockAIProvider()

    if normalized == "openai":
        try:
            from .openai_provider import OpenAIProvider
        except ImportError as exc:
            raise ImportError(
                "OpenAI provider requires the 'openai' package. "
                "Install it with: pip install openai"
            ) from exc
        return OpenAIProvider()

    if normalized == "puter":
        try:
            from .puter_provider import PuterAIProvider
        except ImportError as exc:
            raise ImportError(
                "Puter provider is not available." 
            ) from exc
        return PuterAIProvider()

    raise ValueError(
        f"Unsupported AI provider: {name!r}. "
        "Supported providers: mock, openai, puter, databricks."
    )