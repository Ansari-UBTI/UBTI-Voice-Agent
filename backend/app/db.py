"""Microsoft Fabric SQL Database access (Entra ID token auth via pyodbc)."""
import datetime
import decimal
import json
import struct
import threading
import time
import uuid

import pyodbc

from .config import settings

SQL_COPT_SS_ACCESS_TOKEN = 1256
_local = threading.local()
_lock = threading.Lock()
_cred = None
_token = None


def _credential():
    global _cred
    if _cred is None:
        from azure.identity import (ClientSecretCredential, DefaultAzureCredential,
                                    InteractiveBrowserCredential, ManagedIdentityCredential)
        mode = settings.FABRIC_AUTH_MODE.lower()
        if mode == "sp":
            _cred = ClientSecretCredential(settings.AZURE_TENANT_ID, settings.AZURE_CLIENT_ID,
                                           settings.AZURE_CLIENT_SECRET)
        elif mode == "msi":       # Azure Container Apps managed identity (user-assigned: set AZURE_CLIENT_ID)
            _cred = ManagedIdentityCredential(client_id=settings.AZURE_CLIENT_ID or None)
        elif mode == "interactive":
            _cred = InteractiveBrowserCredential(tenant_id=settings.AZURE_TENANT_ID or None)
        else:
            _cred = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return _cred


def _token_struct() -> bytes:
    global _token
    with _lock:
        if _token is None or _token.expires_on - time.time() < 300:
            _token = _credential().get_token("https://database.windows.net/.default")
    raw = _token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def _connect():
    cs = (f"Driver={{{settings.ODBC_DRIVER}}};Server=tcp:{settings.FABRIC_SQL_SERVER},1433;"
          f"Database={settings.FABRIC_SQL_DATABASE};Encrypt=yes;TrustServerCertificate=no;"
          "Connection Timeout=30;")
    return pyodbc.connect(cs, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()}, autocommit=True)


def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = _connect()
        _local.conn = c
    return c


def _run(fn):
    for attempt in range(2):
        try:
            return fn(_conn())
        except pyodbc.OperationalError:
            _local.conn = None
            if attempt == 1:
                raise
        except pyodbc.Error as e:
            # broken connection codes -> reconnect once
            if "08S01" in str(e) or "08001" in str(e):
                _local.conn = None
                if attempt == 0:
                    continue
            raise


def _clean(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime.datetime):
        return v.replace(microsecond=0).isoformat() + "Z"
    return v


def query(sql: str, *params):
    def f(c):
        cur = c.cursor()
        cur.execute(sql, *params)
        if cur.description is None:
            return []
        cols = [d[0] for d in cur.description]
        return [{k: _clean(v) for k, v in zip(cols, r)} for r in cur.fetchall()]
    return _run(f)


def one(sql: str, *params):
    rows = query(sql, *params)
    return rows[0] if rows else None


def scalar(sql: str, *params):
    r = one(sql, *params)
    return list(r.values())[0] if r else None


def execute(sql: str, *params) -> int:
    def f(c):
        cur = c.cursor()
        cur.execute(sql, *params)
        return cur.rowcount
    return _run(f)


def new_id() -> str:
    return str(uuid.uuid4())


def now() -> datetime.datetime:
    return datetime.datetime.utcnow()


def _val(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v)
    if isinstance(v, bool):
        return 1 if v else 0
    return v


def insert(table: str, data: dict) -> dict:
    data = dict(data)
    data.setdefault("id", new_id())
    cols = ", ".join(f"[{col}]" for col in data)
    qs = ", ".join("?" for _ in data)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", *[_val(v) for v in data.values()])
    return data


def insert_many(table: str, rows: list[dict], batch: int = 500) -> int:
    """Bulk insert (one round trip per batch instead of per row). All rows must have the same keys."""
    if not rows:
        return 0
    rows = [{"id": new_id(), **r} for r in rows]
    cols = list(rows[0].keys())
    sql = f"INSERT INTO {table} ({', '.join(f'[{c}]' for c in cols)}) VALUES ({', '.join('?' for _ in cols)})"

    def f(c):
        cur = c.cursor()
        cur.fast_executemany = True
        for i in range(0, len(rows), batch):
            cur.executemany(sql, [[_val(r[k]) for k in cols] for r in rows[i:i + batch]])
        return len(rows)
    return _run(f)


def update(table: str, row_id: str, data: dict, tenant_id: str | None = None) -> int:
    if not data:
        return 0
    sets = ", ".join(f"[{k}] = ?" for k in data)
    params = [_val(v) for v in data.values()] + [row_id]
    sql = f"UPDATE {table} SET {sets} WHERE id = ?"
    if tenant_id:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    return execute(sql, *params)


def audit(tenant_id, user_id, action: str, detail: str = ""):
    try:
        insert("audit_logs", {"tenant_id": tenant_id, "user_id": user_id, "action": action,
                              "detail": detail[:1000], "created_at": now()})
    except Exception:
        pass
