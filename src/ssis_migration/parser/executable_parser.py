from __future__ import annotations

from typing import Any
import base64
import hashlib

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
        elif any('expressiontask' in value.casefold() for value in (executable_type,creation_name)):
            for child in element.iter():
                if local_name(child.tag).casefold()=='expressiontask':
                    result['properties'].update({local_name(k):v for k,v in child.attrib.items()})
        elif any('sendmailtask' in value.casefold() for value in (executable_type,creation_name)):
            for child in element.iter():
                if local_name(child.tag).casefold()=='sendmailtaskdata':
                    result['properties']={local_name(k):v for k,v in child.attrib.items()}
        elif any('scripttask' in value.casefold() for value in (executable_type,creation_name)):
            sources=[];binary=[]
            for child in element.iter():
                attrs={local_name(k):v for k,v in child.attrib.items()}
                tag=local_name(child.tag).casefold()
                if tag=='scripttaskdata':result['properties'].update(attrs)
                if tag=='scriptproject':
                    result['properties'].update({k:v for k,v in attrs.items() if k in {
                        'Name','Language','VSTAMajorVersion','VSTAMinorVersion','ReadOnlyVariables','ReadWriteVariables'}})
                name=attrs.get('Name') or attrs.get('name') or attrs.get('FileName') or ''
                if name.casefold().endswith(('.cs','.vb')):
                    text=''.join(child.itertext()).strip()
                    if not text:continue
                    encoding=str(attrs.get('Encoding') or attrs.get('encoding') or '').casefold()
                    status='CAPTURED'
                    if encoding=='base64':
                        try:
                            raw=base64.b64decode(''.join(text.split()),validate=True)
                            if len(raw)>1024*1024:raise ValueError('Script source exceeds extraction limit')
                            text=raw.decode('utf-16' if raw.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig')
                        except (ValueError,UnicodeError):status='ENCODING_REVIEW_REQUIRED';text=None
                    sources.append({'name':name,'status':status,'text':text,
                        'sha256':hashlib.sha256(text.encode()).hexdigest() if text is not None else None})
                elif tag in {'binaryitem','binarycode'}:
                    binary.append({'name':name,'encoded_sha256':hashlib.sha256(''.join(child.itertext()).encode()).hexdigest()})
            result['properties'].update(script_sources=sources,script_binaries=binary)

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

            for binding in child:
                kind = local_name(binding.tag).lower()
                if kind in {"parameterbinding", "resultbinding"}:
                    key = "parameter_bindings" if kind == "parameterbinding" else "result_bindings"
                    properties.setdefault(key, []).append({local_name(k): v for k, v in binding.attrib.items()})

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
