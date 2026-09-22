from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DatabaseColumn:
    schema: str
    table: str
    name: str
    data_type: str
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    nullable: bool | None = None
    ordinal: int | None = None


@dataclass
class DatabaseTable:
    schema: str
    name: str
    columns: list[DatabaseColumn] = field(default_factory=list)


@dataclass
class DatabaseView:
    schema: str
    name: str
    definition: str | None = None


@dataclass
class DatabaseProcedure:
    schema: str
    name: str
    definition: str | None = None
    parameters: list[dict[str, Any]] = field(default_factory=list)
    dependencies: list[dict[str, Any]] = field(default_factory=list)
    create_date: str | None = None
    modify_date: str | None = None
    encrypted: bool = False


@dataclass
class DatabaseFunction:
    schema: str
    name: str
    function_type: str | None = None
    definition: str | None = None
    parameters: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class DatabaseDependency:
    referencing_schema: str
    referencing_object: str
    referenced_schema: str | None
    referenced_object: str
    referenced_type: str | None = None


@dataclass
class DatabaseMetadata:
    database: dict[str, Any] = field(default_factory=dict)
    schemas: list[str] = field(default_factory=list)
    tables: list[DatabaseTable] = field(default_factory=list)
    views: list[DatabaseView] = field(default_factory=list)
    procedures: list[DatabaseProcedure] = field(default_factory=list)
    functions: list[DatabaseFunction] = field(default_factory=list)
    dependencies: list[DatabaseDependency] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)