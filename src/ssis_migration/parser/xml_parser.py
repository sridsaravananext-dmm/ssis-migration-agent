from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from .namespace import extract_namespaces, local_name


class SSISXMLParser:
    """
    Low-level XML parser.

    Responsibilities:
    - Read a DTSX file
    - Parse XML
    - Expose root element
    - Provide namespace-independent traversal
    """

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)

        if not self.file_path.exists():
            raise FileNotFoundError(
                f"DTSX file not found: {self.file_path}"
            )

        if self.file_path.suffix.lower() not in {".dtsx", ".xml", ".txt"}:
            raise ValueError(
                f"Unsupported file type: {self.file_path.suffix}"
            )

        self.xml_text = self.file_path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        self.root = ET.fromstring(self.xml_text)

        self.namespaces = extract_namespaces(
            self.xml_text
        )

    def find_all(self, element_name: str):
        """
        Find all elements by local XML name,
        independent of namespace.
        """

        return [
            element
            for element in self.root.iter()
            if local_name(element.tag) == element_name
        ]

    def find_first(self, element_name: str):
        """
        Find first element by local XML name.
        """

        for element in self.root.iter():

            if local_name(element.tag) == element_name:
                return element

        return None

    def get_attribute(
        self,
        element: ET.Element,
        attribute_name: str,
    ) -> str | None:

        for key, value in element.attrib.items():

            if local_name(key) == attribute_name:
                return value

        return None

    def children(
        self,
        element: ET.Element,
        element_name: str | None = None,
    ):

        if element_name is None:
            return list(element)

        return [
            child
            for child in element
            if local_name(child.tag) == element_name
        ]