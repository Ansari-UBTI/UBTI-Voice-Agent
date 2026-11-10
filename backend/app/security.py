import base64
import hashlib
import hmac
import os
import secrets
import time

import jwt
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, Header, HTTPException, Request

from . import db
from .config import settings


# ---------------- passwords & sessions ----------------

def hash_password(p: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", p.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_password(p: str, h: str) -> bool:
    try:
        _, s, d = h.split("$")
        dk = hashlib.pbkdf2_hmac("sha256", p.encode(), bytes.fromhex(s), 200_000)
        return hmac.compare_digest(dk.hex(), d)
    except Exception:
        return False


def create_token(user: dict) -> str:
    payload = {"sub": user["id"], "tid": user["tenant_id"], "adm": bool(user.get("is_superadmin")),
               "exp": int(time.time()) + settings.JWT_EXPIRE_HOURS * 3600}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


USER_SQL = ("SELECT u.id, u.tenant_id, u.email, u.name, u.role, u.is_superadmin, u.created_at, "
            "t.name AS tenant_name, t.status AS tenant_status, t.[plan], t.country "
            "FROM users u JOIN tenants t ON t.id = u.tenant_id ")


def user_from_token(token: str) -> dict | None:
    try:
        p = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    except Exception:
        return None
    return db.one(USER_SQL + "WHERE u.id = ? AND u.is_active = 1", p["sub"])


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def current_user(authorization: str | None = Header(None), x_api_key: str | None = Header(None)) -> dict:
    user = None
    if x_api_key:
        k = db.one("SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", sha(x_api_key))
        if k:
            user = db.one(USER_SQL + "WHERE u.tenant_id = ? AND u.role = 'owner'", k["tenant_id"])
            db.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", db.now(), k["id"])
    elif authorization and authorization.lower().startswith("bearer "):
        user = user_from_token(authorization[7:])
    if not user:
        raise HTTPException(401, "Not authenticated")
    if user["tenant_status"] == "suspended" and not user["is_superadmin"]:
        raise HTTPException(403, "Your account is suspended. Contact UBTI support.")
    return user


# ---------------- roles ----------------
# viewer: read only | member: build agents, knowledge, calls, batches | admin/owner: numbers, keys, team, privacy

RANK = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}


def has_role(user: dict, role: str) -> bool:
    return bool(user.get("is_superadmin")) or RANK.get(user.get("role") or "viewer", 0) >= RANK[role]


def need(role: str):
    def dep(user: dict = Depends(current_user)) -> dict:
        if not has_role(user, role):
            raise HTTPException(403, f"Your role ({user.get('role')}) cannot do this - {role} access needed")
        return user
    return dep


member = need("member")
admin = need("admin")


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user["is_superadmin"]:
        raise HTTPException(403, "Admin only")
    return user


# ---------------- encryption of stored secrets ----------------

def _fernet_for(secret: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))


def encrypt(s: str) -> str:
    return _fernet_for(settings.ENCRYPTION_KEY or settings.JWT_SECRET).encrypt(s.encode()).decode() if s else ""


def decrypt(s: str) -> str:
    if not s:
        return ""
    for secret in filter(None, (settings.ENCRYPTION_KEY, settings.JWT_SECRET, settings.OLD_JWT_SECRET)):
        try:
            return _fernet_for(secret).decrypt(s.encode()).decode()
        except (InvalidToken, ValueError):
            continue
    return ""


# ---------------- signed callback / media URLs ----------------

def _webhook_key() -> bytes:
    return (settings.WEBHOOK_SECRET or ("wh:" + settings.JWT_SECRET)).encode()


def sign(value: str) -> str:
    return hmac.new(_webhook_key(), value.encode(), hashlib.sha256).hexdigest()[:32]


def check_sig(value: str, sig: str | None) -> bool:
    if sig and hmac.compare_digest(sign(value), sig):
        return True
    return settings.ALLOW_UNSIGNED_WEBHOOKS and not sig


def platform_key() -> str:
    """Key for webhooks that are not tied to one tenant (e.g. the platform's own ACS resource)."""
    return sign("platform-inbound")


def tenant_key(tenant_id: str) -> str:
    t = db.one("SELECT webhook_key FROM tenants WHERE id = ?", tenant_id)
    if t and t.get("webhook_key"):
        return t["webhook_key"]
    k = secrets.token_urlsafe(18)
    db.execute("UPDATE tenants SET webhook_key = ? WHERE id = ?", k, tenant_id)
    return k


def check_production_settings():
    """Refuse to run with the sample secret outside DEV_MODE."""
    weak = settings.JWT_SECRET in ("", "change-me") or len(settings.JWT_SECRET) < 32
    if weak and not settings.DEV_MODE:
        raise RuntimeError("JWT_SECRET is missing or weak. Set a random value of 32+ characters "
                           "(python -c \"import secrets;print(secrets.token_urlsafe(48))\"), or DEV_MODE=true locally.")


def client_ip(req: Request) -> str:
    return (req.headers.get("x-forwarded-for") or (req.client.host if req.client else "")).split(",")[0].strip()
