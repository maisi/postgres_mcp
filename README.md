# postgres-readonly-mcp

Read-only MCP server for Postgres to support reporting agents.

## Requirements
- Python 3.10+
- pip install -r requirements.txt

## Run
- Set DATABASE_URL (recommended) or PGHOST/PGPORT/PGDATABASE/PGUSER/PGPASSWORD
- python server.py

Example:
  set DATABASE_URL=postgresql://user:pass@host:5432/dbname
  python server.py

## Docker
- docker build -t postgres-readonly-mcp .
- docker run --rm -p 8000:8000 -e DATABASE_URL=postgresql://user:pass@host:5432/dbname postgres-readonly-mcp

## OpenWebUI
- Add an MCP server using Streamable HTTP.
- Server URL: http://<host>:8000/mcp/ (note the trailing slash)
- Ensure the container sets MCP_TRANSPORT=streamable-http and FASTMCP_HOST=0.0.0.0

## Tools
- list_schemas
- list_tables(schema)
- describe_table(schema, table)
- list_foreign_keys(schema=None)
- run_query(sql, max_rows=200)

## Environment options
- MCP_TRANSPORT (stdio|sse|streamable-http, default stdio)
- MCP_MOUNT_PATH (optional, used for sse transport)
- MCP_MAX_ROWS (default 200)
- MCP_MAX_ROWS_CAP (default 1000)
- MCP_STATEMENT_TIMEOUT_MS (default 5000)
- MCP_LOCK_TIMEOUT_MS (default 5000)
- MCP_CONNECT_TIMEOUT_S (default 5)
- MCP_POOL_MIN (default 1)
- MCP_POOL_MAX (default 5)
- MCP_APP_NAME (default postgres-readonly-mcp)
- FASTMCP_HOST (default 127.0.0.1)
- FASTMCP_PORT (default 8000)
