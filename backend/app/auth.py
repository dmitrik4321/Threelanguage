from __future__ import annotations
import os, time, uuid
import jwt  # pyjwt
from argon2 import PasswordHasher
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .db import get_db
from .models import User

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me")
ph = PasswordHasher()

class Credentials(BaseModel):
    email: EmailStr
    password: str

def _token(user_id: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + 90 * 86400},
                      JWT_SECRET, algorithm="HS256")

# --- корректные зависимости ---
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
bearer = HTTPBearer(auto_error=False)

async def get_current_user(cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                           db: AsyncSession = Depends(get_db)) -> User:
    if cred is None:
        raise HTTPException(401, "no token")
    try:
        payload = jwt.decode(cred.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "bad token")
    u = await db.get(User, int(payload["sub"]))
    if not u:
        raise HTTPException(401, "no user")
    return u

register = APIRouter()
login = APIRouter()

@register.post("/register")
async def reg(c: Credentials, db: AsyncSession = Depends(get_db)):
    exists = await db.scalar(select(User).where(User.email == c.email))
    if exists:
        raise HTTPException(409, "email taken")
    u = User(email=c.email, pw_hash=ph.hash(c.password), created_at_ms=int(time.time() * 1000))
    db.add(u)
    await db.commit()
    return {"token": _token(u.id), "user_id": u.id}

@login.post("/login")
async def log(c: Credentials, db: AsyncSession = Depends(get_db)):
    u = await db.scalar(select(User).where(User.email == c.email))
    if not u:
        raise HTTPException(401, "bad creds")
    try:
        ph.verify(u.pw_hash, c.password)
    except Exception:
        raise HTTPException(401, "bad creds")
    return {"token": _token(u.id)}
