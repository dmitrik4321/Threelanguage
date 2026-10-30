"""Курсы/колоды: каталог, выдача bundle, импорт .apkg (v0.1)."""
from __future__ import annotations
import json, zipfile, io
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import CourseMeta, User
from ..auth import get_current_user

router = APIRouter()

@router.get("/catalog")
async def catalog(lang: str | None = None, db: AsyncSession = Depends(get_db)):
    q = select(CourseMeta).where(CourseMeta.published == True)  # noqa: E712
    if lang:
        q = q.where(CourseMeta.lang == lang)
    rows = (await db.scalars(q)).all()
    return [{"id": r.id, "lang": r.lang, "title": r.title, "level_scheme": r.level_scheme,
             "version": r.version} for r in rows]

@router.get("/{course_id}/bundle")
async def bundle(course_id: int, db: AsyncSession = Depends(get_db)):
    c = await db.get(CourseMeta, course_id)
    if not c:
        raise HTTPException(404, "no course")
    return {"format_version": 1, "lang": c.lang, "level_scheme": c.level_scheme,
            "version": c.version, "cards": c.deck_json["cards"] if c.deck_json else []}

@router.post("/import_apkg")
async def import_apkg(file: UploadFile = File(...), user: User = Depends(get_current_user),
                      db: AsyncSession = Depends(get_db)):
    """Загрузка .apkg; конвертация в deck_format.json — tools/apkg_convert.py (v0.2 заглушка)."""
    raw = await file.read()
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(422, "not a valid apkg/zip")
    names = zf.namelist()
    if "collection.anki2" not in names and "deck.json" not in names:
        raise HTTPException(422, "no recognizable deck payload")
    # TODO(v0.2): распарсить sqlite collection.anki2 → cards[] по shared/schema/deck_format.json
    c = CourseMeta(lang="und", title=file.filename or "imported", source="apkg_import",
                   author_id=user.id, published=False, deck_json={"cards": []})
    db.add(c)
    await db.commit()
    return {"course_id": c.id, "status": "pending_conversion"}
