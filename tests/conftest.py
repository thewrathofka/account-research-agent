"""pytest session-level setup.

Force unit tests onto the local SQLite path, even when a real Turso URL +
token exist in `.env`. Without this, `config.py:load_dotenv()` (which fires
at import time) re-populates LIBSQL_URL from .env and every test routes its
isolated tmp_path "runs.db" to the shared production Turso DB — tests
contaminate each other and read real production data.

The trick: set the env vars to empty strings BEFORE config.py imports.
python-dotenv's `load_dotenv()` defaults to `override=False`, so it won't
replace already-set values, and `os.environ.get("LIBSQL_URL")` evaluates
the empty string as falsy in run_log._open_conn's truthiness check
(`if libsql_url:` → False for "" → falls through to local sqlite).
"""

from __future__ import annotations

import os

# Set BEFORE any import of config (and thus load_dotenv).
os.environ["LIBSQL_URL"] = ""
os.environ["LIBSQL_AUTH_TOKEN"] = ""
