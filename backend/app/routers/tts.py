"""TTS-прокси: кэш mp3 (espeak-ng локально; для CJK — подключим edge/google voices позже)."""
from __future__ import annotations
import hashlib, os, subprocess, tempfile
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import TtsCache
from ..auth import get_current_user

router = APIRouter()
CACHE_DIR = os.environ.get("TTS_CACHE_DIR", tempfile.gettempdir() + "/tl_tts")
os.makedirs(CACHE_DIR, exist_ok=True)

@router.get("/{lang}")
async def speak(lang: str, text: str, db: AsyncSession = Depends(get_db),
                user=Depends(get_current_user)):
    h = hashlib.sha256(f"{lang}|{text}".encode()).hexdigest()
    path = os.path.join(CACHE_DIR, h + ".wav")
    if not os.path.exists(path):
        try:
            subprocess.run(["espeak-ng", "-v", lang, "-w", path, text],
                           check=True, capture_output=True, timeout=10)
        except FileNotFoundError:
            raise HTTPException(501, "espeak-ng not installed on server")
        except subprocess.CalledProcessError:
            raise HTTPException(422, "tts failed")
        row = TtsCache(lang=lang, text_hash=h, mp3_path=path)
        db.add(row)
        await db.commit()
    return FileResponse(path, media_type="audio/wav")
