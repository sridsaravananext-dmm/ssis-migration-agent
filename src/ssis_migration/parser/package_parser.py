from __future__ import annotations

from pathlib import Path
from typing import Any

from .pipeline_parser import SSISPipelineParser
from .xml_parser import SSISXMLParser
from .namespace import local_name
from .executable_parser import SSISExecutableParser


class SSISPackageParser:
    """
    Generic SSIS DTSX package parser.

    Extracts structural information only.
    No WWI-specific business logic.
    """

    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self.xml = SSISXMLParser(self.file_path)
        self.root = self.xml.root
        self.pipeline_parser = SSISPipelineParser()
        self.executable_parser = SSISExecutableParser()

    # =========================================================
    # PUBLIC API
    # =========================================================

    def parse(self) -> dict[str, Any]:
        return {
            "source": self._parse_source(),
            "package": self._parse_package(),
            "executables": self._parse_executables(),
            "precedence_constraints": self._parse_precedence_constraints(),
            "variables": self._parse_variables(),
            "connection_manager_references":
                self._parse_connection_manager_references(),
            "data_flow_tasks": self._parse_data_flow_tasks(),
            "statistics": self._build_statistics(),
        }

    # =========================================================
    # SOURCE
    # =========================================================

    def _parse_source(self) -> dict[str, Any]:
        return {
            "file_name": self.file_path.name,
            "file_path": str(self.file_path),
            "file_extension": self.file_path.suffix,
        }

    # =========================================================
    # PACKAGE
    # =========================================================

    def _parse_package(self) -> dict[str, Any]:
        return {
            "xml_tag": local_name(self.root.tag),
            "attributes": self._attributes(self.root),
        }

        # =========================================================
    # EXECUTABLES
    # =========================================================

    def _parse_executables(self) -> list[dict[str, Any]]:
        results = []

        for element in self.xml.find_all("Executable"):
            results.append(
                self._parse_executable(element)
            )

        return results

    def _parse_executable(
        self,
        element,
    ) -> dict[str, Any]:

        attributes = self._attributes(element)

        executable_type = self._get_attribute(
            element,
            "ExecutableType",
        )

        creation_name = self._get_attribute(
            element,
            "CreationName",
        )

        executable_specific = (
            self.executable_parser.parse(
                element
            )
        )

        properties = executable_specific.get(
            "properties",
            {},
        )

        # Resolve Execute SQL Task connection IDs
        # to logical SSIS project connection references.
        self._resolve_executable_connection(
            properties
        )

        return {
            "tag": local_name(element.tag),

            "attributes": attributes,

            "name": self._get_attribute(
                element,
                "ObjectName",
            ),

            "creation_name": creation_name,

            "executable_type": executable_type,

            "description": self._get_attribute(
                element,
                "Description",
            ),

            "properties": properties,
        }

    def _resolve_executable_connection(
        self,
        properties: dict[str, Any],
    ) -> None:

        connection_id = properties.get("Connection")

        if not connection_id:
            return

        normalized_id = (
            self._normalize_connection_manager_id(
                connection_id
            )
        )

        if not normalized_id:
            return

        for element in self.root.iter():

            element_connection_id = None
            element_connection_ref_id = None

            for key, value in element.attrib.items():

                attribute_name = local_name(key).lower()

                if attribute_name == "connectionmanagerid":
                    element_connection_id = (
                        self._normalize_connection_manager_id(
                            value
                        )
                    )

                elif attribute_name == "connectionmanagerrefid":
                    element_connection_ref_id = value

            if (
                element_connection_id == normalized_id
                and element_connection_ref_id
            ):
                properties[
                    "connection_manager_id"
                ] = normalized_id

                properties[
                    "connection_manager_ref_id"
                ] = element_connection_ref_id

                return

    # =========================================================
    # PRECEDENCE CONSTRAINTS
    # =========================================================

    def _parse_precedence_constraints(
        self,
    ) -> list[dict[str, Any]]:

        results = []

        for element in self.xml.find_all(
            "PrecedenceConstraint"
        ):
            results.append(
                {
                    "tag": local_name(element.tag),
                    "attributes": self._attributes(element),
                    "from": self._get_attribute(
                        element,
                        "From",
                    ),
                    "to": self._get_attribute(
                        element,
                        "To",
                    ),
                    "logical_and": self._get_attribute(
                        element,
                        "LogicalAnd",
                    ),
                    "evaluation_operation":
                        self._get_attribute(
                            element,
                            "EvalOp",
                        ),
                    "value":
                        self._get_attribute(
                            element,
                            "Value",
                        ),
                    "expression":
                        self._get_attribute(
                            element,
                            "Expression",
                        ),
                }
            )

        return results

    # =========================================================
    # VARIABLES
    # =========================================================

    def _parse_variables(self) -> list[dict[str, Any]]:
        results = []

        for element in self.xml.find_all("Variable"):

            # SSIS stores the data type and value on a
            # <VariableValue> child element, not on the
            # <Variable> element itself.
            data_type = None
            value = None

            for child in element:
                if (
                    local_name(child.tag)
                    == "VariableValue"
                ):
                    data_type = (
                        self._get_attribute(
                            child,
                            "DataType",
                        )
                    )
                    text = child.text
                    if text and text.strip():
                        value = text.strip()
                    break

            results.append(
                {
                    "tag": local_name(element.tag),
                    "attributes": self._attributes(element),
                    "name": self._get_attribute(
                        element,
                        "ObjectName",
                    ),
                    "namespace": self._get_attribute(
                        element,
                        "Namespace",
                    ),
                    "data_type": data_type,
                    "value": value,
                    "expression": self._get_attribute(
                        element,
                        "Expression",
                    ),
                    "ref_id": self._get_attribute(
                        element,
                        "DTSID",
                    ),
                }
            )

        return results

        # =========================================================
    # CONNECTION MANAGER REFERENCES
    # =========================================================

    def _parse_connection_manager_references(
        self,
    ) -> list[dict[str, Any]]:

        references = {}

        for element in self.root.iter():

            connection_manager_id = None
            connection_manager_ref_id = None

            for key, value in element.attrib.items():

                attribute_name = local_name(key).lower()

                if attribute_name == "connectionmanagerid":
                    connection_manager_id = value

                elif attribute_name == "connectionmanagerrefid":
                    connection_manager_ref_id = value

            if connection_manager_ref_id:

                normalized_id = self._normalize_connection_manager_id(
                    connection_manager_id
                )

                references[connection_manager_ref_id] = {
                    "reference_id": connection_manager_ref_id,
                    "connection_manager_id": normalized_id,
                    "connection_manager_ref_id": connection_manager_ref_id,
                    "used_by_tag": local_name(element.tag),
                }

        return list(references.values())
    
    # =========================================================
    # DATA FLOW TASKS
    # =========================================================

    def _parse_data_flow_tasks(
    self,
    ) -> list[dict[str, Any]]:

        results = []

        for executable in self.xml.find_all(
            "Executable"
        ):

            creation_name = self._get_attribute(
                executable,
                "CreationName",
            )

            executable_type = self._get_attribute(
                executable,
                "ExecutableType",
            )

            if not self._is_pipeline(
                creation_name,
                executable_type,
            ):
                continue

            results.append(
                self.pipeline_parser.parse(
                    executable
                )
            )

        return results

    def _parse_data_flow_task(
        self,
        element,
    ) -> dict[str, Any]:

        return {
            "name": self._get_attribute(
                element,
                "ObjectName",
            ),
            "creation_name": self._get_attribute(
                element,
                "CreationName",
            ),
            "executable_type": self._get_attribute(
                element,
                "ExecutableType",
            ),
            "attributes": self._attributes(
                element
            ),
        }

    def _is_pipeline(
        self,
        creation_name: str | None,
        executable_type: str | None,
    ) -> bool:

        values = [
            creation_name,
            executable_type,
        ]

        return any(
            value
            and "pipeline" in value.lower()
            for value in values
        )

    # =========================================================
    # HELPERS
    # =========================================================

    def _attributes(
        self,
        element,
    ) -> dict[str, str]:

        return {
            local_name(key): value
            for key, value in element.attrib.items()
        }

    def _get_attribute(
        self,
        element,
        attribute_name: str,
    ) -> str | None:

        for key, value in element.attrib.items():

            if (
                local_name(key).lower()
                == attribute_name.lower()
            ):
                return value

        return None

    def _element_value(
        self,
        element,
    ) -> str | None:

        text = "".join(
            element.itertext()
        ).strip()

        return text or None

    def _normalize_connection_manager_id(
            self,
            connection_manager_id: str | None,
    ) -> str | None:

        if not connection_manager_id:
                            return None
            
        return connection_manager_id.removesuffix(":external")
    
    # =========================================================
    # STATISTICS
    # =========================================================

    def _build_statistics(self) -> dict[str, int]:

        return {
            "executables":
                len(
                    self.xml.find_all("Executable")
                ),

            "precedence_constraints":
                len(
                    self.xml.find_all(
                        "PrecedenceConstraint"
                    )
                ),

            "variables":
                len(
                    self.xml.find_all("Variable")
                ),

            "connection_manager_references":
                len(
                    self._parse_connection_manager_references()
                ),

            "data_flow_tasks":
                len(
                    self._parse_data_flow_tasks()
                ),
        }