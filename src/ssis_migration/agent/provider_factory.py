from .mock_provider import MockAIProvider
from .openai_provider import OpenAIProvider
from .puter_provider import PuterAIProvider
from .provider import AIProvider


def create_provider(name: str) -> AIProvider:
    normalized = name.strip().lower()

    if normalized == "mock":
        return MockAIProvider()

    if normalized == "openai":
        return OpenAIProvider()

    if normalized == "puter":
        return PuterAIProvider()

    raise ValueError(
        f"Unsupported AI provider: {name!r}. "
        "Supported providers: mock, openai, puter."
    )