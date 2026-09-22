from __future__ import annotations

import pyodbc


def create_connection(
    server: str,
    database: str,
    username: str | None = None,
    password: str | None = None,
    trusted_connection: bool = True,
) -> pyodbc.Connection:

    if trusted_connection:
        connection_string = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={server};"
            f"DATABASE={database};"
            "Trusted_Connection=yes;"
            "TrustServerCertificate=yes;"
        )
    else:
        if not username or not password:
            raise ValueError(
                "username and password are required "
                "when trusted_connection=False"
            )

        connection_string = (
            "DRIVER={ODBC Driver 18 for SQL Server};"
            f"SERVER={server};"
            f"DATABASE={database};"
            f"UID={username};"
            f"PWD={password};"
            "TrustServerCertificate=yes;"
        )

    return pyodbc.connect(connection_string)