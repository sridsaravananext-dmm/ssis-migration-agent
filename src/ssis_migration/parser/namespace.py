from __future__ import annotations

import re


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]

    return tag


def namespace_uri(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]

    return None


def extract_namespaces(xml_text: str) -> dict[str, str]:
    """
    Extract xmlns declarations without depending on
    hardcoded SSIS namespace URLs.
    """

    namespaces: dict[str, str] = {}

    pattern = re.compile(
        r'xmlns(?::([A-Za-z_][\w.-]*))?="([^"]+)"'
    )

    for prefix, uri in pattern.findall(xml_text):
        namespaces[prefix or "default"] = uri

    return namespaces