from __future__ import annotations

from typing import Any

from .namespace import local_name


class SSISPipelineParser:
    """
    Generic SSIS Data Flow pipeline parser.

    Extracts:
    - Components
    - Component types
    - Properties
    - Connections
    - Inputs / outputs
    - Input columns
    - Output columns
    - External metadata columns
    - Source -> destination lineage mappings
    - Data flow paths
    """

    def parse(
        self,
        executable,
    ) -> dict[str, Any]:

        components = self._parse_components(executable)

        return {
            "name": self._get_attribute(
                executable,
                "ObjectName",
            ),
            "creation_name": self._get_attribute(
                executable,
                "CreationName",
            ),
            "components": components,
            "paths": self._parse_paths(executable),
            "column_mappings": self._build_column_mappings(
                components
            ),
        }

    # =========================================================
    # COMPONENTS
    # =========================================================

    def _parse_components(
        self,
        executable,
    ) -> list[dict[str, Any]]:

        components = []

        for element in executable.iter():

            if local_name(element.tag).lower() != "component":
                continue

            components.append(
                self._parse_component(element)
            )

        return components

    def _parse_component(
        self,
        element,
    ) -> dict[str, Any]:

        component = {
            "name": self._get_attribute(
                element,
                "name",
            ),

            "ref_id": self._get_attribute(
                element,
                "refId",
            ),

            "component_class_id":
                self._get_attribute(
                    element,
                    "componentClassID",
                ),

            "component_class_name":
                self._get_attribute(
                    element,
                    "componentClassName",
                ),

            "version":
                self._get_attribute(
                    element,
                    "version",
                ),

            "description":
                self._get_attribute(
                    element,
                    "description",
                ),

            "properties":
                self._parse_properties(element),

            "connection_manager_refs":
                self._parse_connection_manager_refs(
                    element
                ),

            "inputs":
                self._parse_inputs(element),

            "outputs":
                self._parse_outputs(element),

        }

        component["detected_type"] = (
            self._detect_component_type(component)
        )

        return component

    # =========================================================
    # COMPONENT TYPE
    # =========================================================

    def _detect_component_type(
        self,
        component: dict[str, Any],
    ) -> str:

        class_id = (
            component.get(
                "component_class_id"
            )
            or ""
        ).lower()

        description = (
            component.get(
                "description"
            )
            or ""
        ).lower()

        combined = (
            class_id
            + " "
            + description
        )

        if "oledbdestination" in combined:
            return "OLE_DB_DESTINATION"

        if "oledbsource" in combined:
            return "OLE_DB_SOURCE"

        if "flatfile" in combined:
            return "FLAT_FILE"

        if "lookup" in combined:
            return "LOOKUP"

        if "derivedcolumn" in combined:
            return "DERIVED_COLUMN"

        if "conditional" in combined:
            return "CONDITIONAL_SPLIT"

        if "aggregate" in combined:
            return "AGGREGATE"

        if "script" in combined:
            return "SCRIPT"

        return "UNKNOWN"

    # =========================================================
    # PROPERTIES
    # =========================================================

    def _parse_properties(
        self,
        element,
    ) -> dict[str, Any]:

        properties = {}

        for child in element.iter():

            if (
                local_name(child.tag).lower()
                != "property"
            ):
                continue

            name = self._get_attribute(
                child,
                "name",
            )

            if not name:
                continue

            value = "".join(
                child.itertext()
            ).strip()

            properties[name] = value

        return properties

    # =========================================================
    # CONNECTIONS
    # =========================================================

    def _parse_connection_manager_refs(
        self,
        element,
    ) -> list[str]:

        refs = []

        for child in element.iter():

            for key, value in child.attrib.items():

                if (
                    local_name(key).lower()
                    == "connectionmanagerrefid"
                ):
                    if value not in refs:
                        refs.append(value)

        return refs

    # =========================================================
    # INPUTS
    # =========================================================

    def _parse_inputs(
        self,
        element,
    ) -> list[dict[str, Any]]:

        results = []

        for child in element.iter():

            if local_name(child.tag).lower() != "input":
                continue

            results.append(
                {
                    "name":
                        self._get_attribute(
                            child,
                            "name",
                        ),

                    "ref_id":
                        self._get_attribute(
                            child,
                            "refId",
                        ),

                    "attributes":
                        self._attributes(child),

                    "columns":
                        self._parse_input_columns(
                            child
                        ),

                    "external_columns":
                        self._parse_external_columns(
                            child
                        ),
                }
            )

        return results

    # =========================================================
    # OUTPUTS
    # =========================================================

    def _parse_outputs(
        self,
        element,
    ) -> list[dict[str, Any]]:

        results = []

        for child in element.iter():

            if local_name(child.tag).lower() != "output":
                continue

            results.append(
                {
                    "name":
                        self._get_attribute(
                            child,
                            "name",
                        ),

                    "ref_id":
                        self._get_attribute(
                            child,
                            "refId",
                        ),

                    "attributes":
                        self._attributes(child),

                    "columns":
                        self._parse_output_columns(
                            child
                        ),
                }
            )

        return results

    # =========================================================
    # INPUT COLUMNS
    # =========================================================

    def _parse_input_columns(
        self,
        input_element,
    ) -> list[dict[str, Any]]:

        columns = []

        for child in input_element.iter():

            if (
                local_name(child.tag).lower()
                != "inputcolumn"
            ):
                continue

            columns.append(
                {
                    "name":
                        self._get_attribute(
                            child,
                            "cachedName",
                        ),

                    "ref_id":
                        self._get_attribute(
                            child,
                            "refId",
                        ),

                    "lineage_id":
                        self._get_attribute(
                            child,
                            "lineageId",
                        ),

                    "external_metadata_column_id":
                        self._get_attribute(
                            child,
                            "externalMetadataColumnId",
                        ),

                    "data_type":
                        self._get_attribute(
                            child,
                            "cachedDataType",
                        ),

                    "length":
                        self._get_attribute(
                            child,
                            "cachedLength",
                        ),

                    "precision":
                        self._get_attribute(
                            child,
                            "cachedPrecision",
                        ),

                    "scale":
                        self._get_attribute(
                            child,
                            "cachedScale",
                        ),

                    "code_page":
                        self._get_attribute(
                            child,
                            "cachedCodePage",
                        ),
                }
            )

        return columns

    # =========================================================
    # OUTPUT COLUMNS
    # =========================================================

    def _parse_output_columns(
        self,
        output_element,
    ) -> list[dict[str, Any]]:

        columns = []

        for child in output_element.iter():

            if (
                local_name(child.tag).lower()
                != "outputcolumn"
            ):
                continue

            columns.append(
                {
                    "name":
                        self._get_attribute(
                            child,
                            "name",
                        ),

                    "ref_id":
                        self._get_attribute(
                            child,
                            "refId",
                        ),

                    "lineage_id":
                        self._get_attribute(
                            child,
                            "lineageId",
                        ),

                    "data_type":
                        self._get_attribute(
                            child,
                            "dataType",
                        ),

                    "length":
                        self._get_attribute(
                            child,
                            "length",
                        ),

                    "precision":
                        self._get_attribute(
                            child,
                            "precision",
                        ),

                    "scale":
                        self._get_attribute(
                            child,
                            "scale",
                        ),

                    "code_page":
                        self._get_attribute(
                            child,
                            "codePage",
                        ),
                }
            )

        return columns

    # =========================================================
    # EXTERNAL METADATA
    # =========================================================

    def _parse_external_columns(
        self,
        input_element,
    ) -> list[dict[str, Any]]:

        columns = []

        for child in input_element.iter():

            if (
                local_name(child.tag).lower()
                != "externalmetadatacolumn"
            ):
                continue

            columns.append(
                {
                    "name":
                        self._get_attribute(
                            child,
                            "name",
                        ),

                    "ref_id":
                        self._get_attribute(
                            child,
                            "refId",
                        ),

                    "data_type":
                        self._get_attribute(
                            child,
                            "dataType",
                        ),

                    "length":
                        self._get_attribute(
                            child,
                            "length",
                        ),

                    "precision":
                        self._get_attribute(
                            child,
                            "precision",
                        ),

                    "scale":
                        self._get_attribute(
                            child,
                            "scale",
                        ),
                }
            )

        return columns

    # =========================================================
    # COLUMN MAPPINGS
    # =========================================================

    def _build_column_mappings(
        self,
        components: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        mappings = []

        for component in components:

            for input_data in component["inputs"]:

                for column in input_data["columns"]:

                    lineage_id = column.get(
                        "lineage_id"
                    )

                    mappings.append(
                        {
                            "destination_component":
                                component["name"],

                            "destination_input":
                                input_data["name"],

                            "destination_column":
                                column["name"],

                            "destination_column_ref":
                                column["ref_id"],

                            "source_lineage_id":
                                lineage_id,

                            "external_metadata_column_id":
                                column[
                                    "external_metadata_column_id"
                                ],
                        }
                    )

        return mappings

    # =========================================================
    # PATHS
    # =========================================================

    def _parse_paths(
        self,
        executable,
    ) -> list[dict[str, Any]]:

        paths = []

        for element in executable.iter():

            if (
                local_name(element.tag).lower()
                != "path"
            ):
                continue

            paths.append(
                {
                    "name":
                        self._get_attribute(
                            element,
                            "name",
                        ),

                    "start_id":
                        self._get_attribute(
                            element,
                            "startId",
                        ),

                    "end_id":
                        self._get_attribute(
                            element,
                            "endId",
                        ),

                    "attributes":
                        self._attributes(element),
                }
            )

        return paths

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