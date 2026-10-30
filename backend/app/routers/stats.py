"""Агрегаты: дневной прогресс, тренд оценок, аудит АФК."""
from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from ..db import get_db
from ..models import SyncEvent, User, PaceAggregate
from ..auth import get_current_user

router = APIRouter()

@router.get("/day")
async def day(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Σ effective_ms по языкам за текущий учебный день (из event-log payload t=session)."""
    rows = (await db.scalars(select(SyncEvent).where(SyncEvent.user_id == user.id))).all()
    agg: dict = {}
    for r in rows:
        p = r.payload
        if p.get("t") == "session_end":
            key = p.get("day_key", "?")
            lang = p.get("lang_id", "?")
            agg.setdefault((key, lang), 0)
            agg[(key, lang)] += p.get("effective_ms", 0)
    return {"buckets": [{"day_key": k, "lang_id": l, "effective_ms": v}
                        for (k, l), v in agg.items()]}

@router.get("/lesson_scores")
async def lesson_scores(limit: int = 60, user: User = Depends(get_current_user),
                        db: AsyncSession = Depends(get_db)):
    rows = (await db.scalars(select(SyncEvent).where(
        SyncEvent.user_id == user.id).order_by(SyncEvent.seq.desc()).limit(limit * 4))).all()
    out = [r.payload for r in rows if r.payload.get("t") == "session_end"
           and r.payload.get("lesson_score") is not None][:limit]
    return {"scores": out}

@router.get("/afk_audit")
async def afk_audit(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    """Доля АФК-подозрительных попыток — сигнал для антифрода и калибровки порога ×10."""
    total = await db.scalar(select(func.count()).select_from(PaceAggregate)
                            .where(PaceAggregate.user_id == user.id))
    sus = await db.scalar(select(func.count()).select_from(PaceAggregate)
                          .where(PaceAggregate.user_id == user.id,
                                 PaceAggregate.afk_suspect == True))  # noqa: E712
    return {"total_attempts": total or 0, "afk_suspect": sus or 0}
