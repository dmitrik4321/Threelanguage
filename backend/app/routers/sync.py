"""Синхронизация event-log + файл-сейвы (.threesave)."""
from __future__ import annotations
import time, zipfile, io, json
from typing import List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import SyncEvent, User, PaceAggregate
from ..auth import get_current_user

router = APIRouter()

class EventIn(BaseModel):
    client_seq: int
    payload: dict

class PushBody(BaseModel):
    device_id: str
    events: List[EventIn]

@router.post("/push")
async def push(body: PushBody, user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    stored = 0
    for e in body.events:
        dup = await db.scalar(select(SyncEvent).where(
            SyncEvent.user_id == user.id, SyncEvent.device_id == body.device_id,
            SyncEvent.client_seq == e.client_seq))
        if dup:
            continue
        db.add(SyncEvent(user_id=user.id, device_id=body.device_id,
                         client_seq=e.client_seq, payload=e.payload,
                         created_at_ms=int(time.time() * 1000)))
        p = e.payload
        if p.get("t") == "attempt":   # серверная копия темпа для аудита АФК
            db.add(PaceAggregate(user_id=user.id, kind=p.get("kind", "?"),
                                 active_ms=p.get("active_ms", 0), weight_ms=p.get("weight_ms", 0),
                                 afk_suspect=bool(p.get("afk_suspect")),
                                 recorded_at_ms=p.get("answered_at_ms", 0)))
        stored += 1
    await db.commit()
    max_seq = await db.scalar(select(SyncEvent.seq).where(SyncEvent.user_id == user.id)
                              .order_by(SyncEvent.seq.desc()).limit(1))
    return {"accepted": stored, "server_seq": max_seq or 0}

@router.get("/pull")
async def pull(since_seq: int = Query(0, ge=0), user: User = Depends(get_current_user),
               db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(SyncEvent).where(
        SyncEvent.user_id == user.id, SyncEvent.seq > since_seq)
        .order_by(SyncEvent.seq).limit(5000))).all()
    return {"events": [{"seq": r.seq, "device_id": r.device_id,
                        "client_seq": r.client_seq, "payload": r.payload,
                        "created_at_ms": r.created_at_ms} for r in rows],
            "latest_seq": rows[-1].seq if rows else since_seq}

@router.post("/savefile/upload")
async def savefile_upload(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """.threesave = ZIP(manifest.json + state.db + settings.json); валидация структуры."""
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(422, "not a .threesave zip")
    need = {"manifest.json", "state.db", "settings.json"}
    missing = need - set(zf.namelist())
    if missing:
        raise HTTPException(422, f"missing {sorted(missing)}")
    manifest = json.loads(zf.read("manifest.json"))
    if manifest.get("app") != "threelanguage" or "schema_version" not in manifest:
        raise HTTPException(422, "bad manifest")
    # v0.2: применить state.db поверх event-log (LWW). Пока — приём и аудит.
    return {"accepted": True, "bytes": len(raw), "manifest": manifest}

@router.get("/savefile/download")
async def savefile_download(user: User = Depends(get_current_user),
                            db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(SyncEvent).where(SyncEvent.user_id == user.id)
                             .order_by(SyncEvent.seq))).all()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(
            {"app": "threelanguage", "schema_version": 1, "user_id": user.id,
             "exported_at_ms": int(time.time() * 1000), "events": len(rows)}))
        zf.writestr("events.jsonl", "\n".join(json.dumps(
            {"seq": r.seq, "payload": r.payload}) for r in rows))
        zf.writestr("settings.json", "{}")
        # state.db не отдаём сырьём: клиент пересобирает БД из events.jsonl своим ядром
    buf.seek(0)
    return Response(buf.read(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="export.threesave"'})
