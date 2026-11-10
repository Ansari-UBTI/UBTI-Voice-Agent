from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..security import USER_SQL, create_token, current_user, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
PLAN_QUOTA = {"trial": 100, "starter": 1000, "growth": 5000, "enterprise": 50000}


class SignupIn(BaseModel):
    company: str = Field(min_length=2)
    name: str = Field(min_length=2)
    email: str
    password: str = Field(min_length=8)
    country: str = "IN"


class LoginIn(BaseModel):
    email: str
    password: str


@router.post("/signup")
def signup(b: SignupIn):
    email = b.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Invalid email")
    if db.one("SELECT id FROM users WHERE email = ?", email):
        raise HTTPException(400, "Email already registered")
    t = db.insert("tenants", {"name": b.company, "plan": "trial", "status": "active", "country": b.country,
                              "minutes_quota": PLAN_QUOTA["trial"], "created_at": db.now()})
    u = db.insert("users", {"tenant_id": t["id"], "email": email, "name": b.name, "role": "owner",
                            "password_hash": hash_password(b.password), "is_superadmin": False,
                            "is_active": True, "created_at": db.now()})
    db.audit(t["id"], u["id"], "signup", b.company)
    user = db.one(USER_SQL + "WHERE u.id = ?", u["id"])
    return {"token": create_token(user), "user": user}


@router.post("/login")
def login(b: LoginIn):
    row = db.one("SELECT * FROM users WHERE email = ? AND is_active = 1", b.email.strip().lower())
    if not row or not verify_password(b.password, row["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", db.now(), row["id"])
    user = db.one(USER_SQL + "WHERE u.id = ?", row["id"])
    if user["tenant_status"] == "suspended" and not user["is_superadmin"]:
        raise HTTPException(403, "Account suspended. Contact UBTI support.")
    db.audit(row["tenant_id"], row["id"], "login")
    return {"token": create_token(user), "user": user}


@router.get("/me")
def me(u=Depends(current_user)):
    return u
