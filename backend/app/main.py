"""Threelanguage backend — FastAPI (v0.1 skeleton, готов к ревью).

Запуск dev:  uvicorn app.main:app --reload
env: DATABASE_URL (postgres), JWT_SECRET, REDIS_URL(опц.)
"""
from __future__ import annotations
import os, time, uuid, zipfile, io, json
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.security import BearerToken
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db, init_models
from .auth import register, login
from .routers import decks, sync, stats, tts

app = FastAPI(title="Threelanguage API", version="0.1.0")
app.include_router(register.router, prefix="/auth")
app.include_router(login.router, prefix="/auth")
app.include_router(decks.router, prefix="/decks")
app.include_router(sync.router, prefix="/sync")
app.include_router(stats.router, prefix="/stats")
app.include_router(tts.router, prefix="/tts")


@app.on_event("startup")
async def startup():
    async with init_models() as _:
        pass


@app.get("/healthz")
async def healthz():
    return {"ok": True, "ts": int(time.time() * 1000)}
