from __future__ import annotations

from typing import Any

from .namespace import local_name


class SSISExecutableParser:
    """
    Generic parser for SSIS executable-specific properties.
    """

    def parse(self, element) -> dict[str, Any]:

        executable_type = (
            self._get_attribute(
                element,
                "ExecutableType",
            )
            or ""
        )

        creation_name = (
            self._get_attribute(
                element,
                "CreationName",
            )
            or ""
        )

        result = {
            "properties": {},
        }

        if self._is_execute_sql_task(
            executable_type,
            creation_name,
        ):
            result["properties"] = (
                self._parse_execute_sql_task(
                    element
                )
            )

        return result

    # =========================================================
    # EXECUTE SQL TASK
    # =========================================================

    def _parse_execute_sql_task(
        self,
        element,
    ) -> dict[str, Any]:

        properties: dict[str, Any] = {}

        for child in element.iter():

            if local_name(child.tag).lower() != "sqltaskdata":
                continue

            for key, value in child.attrib.items():

                property_name = local_name(key)

                properties[property_name] = value

        return properties

    # =========================================================
    # TYPE DETECTION
    # =========================================================

    @staticmethod
    def _is_execute_sql_task(
        executable_type: str,
        creation_name: str,
    ) -> bool:

        values = (
            executable_type,
            creation_name,
        )

        return any(
            value
            and (
                "executesqltask"
                in value.lower()
                or "execute sql"
                in value.lower()
            )
            for value in values
        )

    # =========================================================
    # XML ATTRIBUTE
    # =========================================================

    @staticmethod
    def _get_attribute(
        element,
        name: str,
    ) -> str | None:

        for key, value in element.attrib.items():

            if (
                local_name(key).lower()
                == name.lower()
            ):
                return value

        return None