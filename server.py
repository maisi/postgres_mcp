import os
import re
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from mcp.server.fastmcp import FastMCP

FASTMCP_HOST = os.getenv("FASTMCP_HOST", "127.0.0.1")
FASTMCP_PORT = int(os.getenv("FASTMCP_PORT", "8000"))

mcp = FastMCP("postgres-readonly", host=FASTMCP_HOST, port=FASTMCP_PORT)

POOL: Optional[ThreadedConnectionPool] = None

DEFAULT_MAX_ROWS = int(os.getenv("MCP_MAX_ROWS", "200"))
MAX_ROWS_CAP = int(os.getenv("MCP_MAX_ROWS_CAP", "1000"))
STATEMENT_TIMEOUT_MS = int(os.getenv("MCP_STATEMENT_TIMEOUT_MS", "5000"))
LOCK_TIMEOUT_MS = int(os.getenv("MCP_LOCK_TIMEOUT_MS", "5000"))
CONNECT_TIMEOUT_S = int(os.getenv("MCP_CONNECT_TIMEOUT_S", "5"))
POOL_MIN = int(os.getenv("MCP_POOL_MIN", "1"))
POOL_MAX = int(os.getenv("MCP_POOL_MAX", "5"))
APP_NAME = os.getenv("MCP_APP_NAME", "postgres-readonly-mcp")

READONLY_BLOCKLIST = re.compile(
    r"\b("
    r"insert|update|delete|merge|create|drop|alter|truncate|"
    r"grant|revoke|copy|vacuum|analyze|refresh|call|do|lock|"
    r"set|reset|listen|notify|cluster|reindex|checkpoint|into|"
    r"for\s+update|for\s+share"
    r")\b",
    re.IGNORECASE,
)


def _connection_kwargs() -> Dict[str, Any]:
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return {"dsn": dsn}

    host = os.getenv("PGHOST", "localhost")
    port = int(os.getenv("PGPORT", "5432"))
    dbname = os.getenv("PGDATABASE", "postgres")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD", "")

    kwargs: Dict[str, Any] = {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
    }
    if password:
        kwargs["password"] = password
    return kwargs


def _get_pool() -> ThreadedConnectionPool:
    global POOL
    if POOL is None:
        minconn = max(1, POOL_MIN)
        maxconn = max(minconn, POOL_MAX)
        options = (
            f"-c default_transaction_read_only=on "
            f"-c statement_timeout={STATEMENT_TIMEOUT_MS} "
            f"-c lock_timeout={LOCK_TIMEOUT_MS}"
        )
        kwargs = _connection_kwargs()
        kwargs["connect_timeout"] = CONNECT_TIMEOUT_S
        kwargs["options"] = options
        kwargs["application_name"] = APP_NAME
        POOL = ThreadedConnectionPool(minconn, maxconn, **kwargs)
    return POOL


def _get_conn():
    pool = _get_pool()
    conn = pool.getconn()
    conn.autocommit = True
    return conn


def _release_conn(conn) -> None:
    if POOL is not None:
        POOL.putconn(conn)


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
    sql = re.sub(r"--.*?$", "", sql, flags=re.M)
    return sql


def _has_multiple_statements(sql: str) -> bool:
    stripped = sql.rstrip().rstrip(";")
    return ";" in stripped


def _normalize_sql(sql: str) -> str:
    return _strip_comments(sql).strip()


def _is_readonly(sql: str) -> bool:
    cleaned = _normalize_sql(sql)
    if not cleaned:
        return False
    if _has_multiple_statements(cleaned):
        return False
    lowered = cleaned.lower()
    if not lowered.startswith(("select", "with", "explain")):
        return False
    if READONLY_BLOCKLIST.search(cleaned):
        return False
    return True


def _clamp_rows(value: int) -> int:
    try:
        rows = int(value)
    except (TypeError, ValueError):
        rows = DEFAULT_MAX_ROWS
    if rows <= 0:
        rows = DEFAULT_MAX_ROWS
    return min(rows, MAX_ROWS_CAP)


def _fetch_all(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, params or ())
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        _release_conn(conn)


@mcp.tool()
def list_schemas() -> List[str]:
    """List non-system schemas."""
    rows = _fetch_all(
        """
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name NOT LIKE 'pg_%%'
          AND schema_name <> 'information_schema'
        ORDER BY schema_name
        """
    )
    return [r["schema_name"] for r in rows]


@mcp.tool()
def list_tables(schema: str = "public") -> List[str]:
    """List tables in a schema."""
    rows = _fetch_all(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = %s
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """,
        (schema,),
    )
    return [r["table_name"] for r in rows]


@mcp.tool()
def describe_table(schema: str, table: str) -> List[Dict[str, Any]]:
    """Describe columns for a table."""
    return _fetch_all(
        """
        SELECT
          column_name,
          data_type,
          is_nullable,
          column_default
        FROM information_schema.columns
        WHERE table_schema = %s AND table_name = %s
        ORDER BY ordinal_position
        """,
        (schema, table),
    )


@mcp.tool()
def list_foreign_keys(schema: Optional[str] = None) -> List[Dict[str, Any]]:
    """List foreign key relationships, optionally filtered by schema."""
    return _fetch_all(
        """
        SELECT
          tc.constraint_name,
          tc.table_schema,
          tc.table_name,
          kcu.column_name,
          ccu.table_schema AS foreign_table_schema,
          ccu.table_name AS foreign_table_name,
          ccu.column_name AS foreign_column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY'
          AND tc.table_schema NOT IN ('pg_catalog', 'information_schema')
          AND (%s IS NULL OR tc.table_schema = %s)
        ORDER BY tc.table_schema, tc.table_name, tc.constraint_name, kcu.ordinal_position
        """,
        (schema, schema),
    )


@mcp.tool()
def run_query(sql: str, max_rows: int = DEFAULT_MAX_ROWS) -> List[Dict[str, Any]]:
    """Run a guarded read-only SQL query."""
    if not _is_readonly(sql):
        raise ValueError("Only single-statement read-only SELECT/WITH/EXPLAIN queries are allowed.")

    cleaned = _normalize_sql(sql).rstrip(";")
    limited_rows = _clamp_rows(max_rows)

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            if cleaned.lower().startswith("explain"):
                cur.execute(cleaned)
            else:
                wrapped = f"SELECT * FROM ({cleaned}) AS _mcp_sub LIMIT %s"
                cur.execute(wrapped, (limited_rows,))
            rows = cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        _release_conn(conn)


def _run_server() -> None:
    transport = os.getenv("MCP_TRANSPORT", "stdio")
    mount_path = os.getenv("MCP_MOUNT_PATH")
    mcp.run(transport=transport, mount_path=mount_path)


if __name__ == "__main__":
    _run_server()
