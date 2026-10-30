from __future__ import annotations
from sqlalchemy import Column, Integer, BigInteger, String, Float, Boolean, Text, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from .db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    pw_hash = Column(String(255), nullable=False)          # argon2id
    native_lang = Column(String(16), default="ru")
    created_at_ms = Column(BigInteger, nullable=False)
    premium = Column(Boolean, default=False)               # место под монетизацию (не в MVP)

class Device(Base):
    __tablename__ = "devices"
    id = Column(String(64), primary_key=True)              # клиентский UUID
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    platform = Column(String(16))                          # android|linux|windows
    last_seen_ms = Column(BigInteger)

class CourseMeta(Base):
    __tablename__ = "course_meta"
    id = Column(Integer, primary_key=True)
    lang = Column(String(16), index=True)
    title = Column(String(255))
    source = Column(String(32))                            # builtin|ugc|apkg_import
    version = Column(Integer, default=1)
    level_scheme = Column(String(16), default="CEFR")
    author_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    license = Column(String(64), default="CC-BY-SA-4.0")
    published = Column(Boolean, default=False)
    deck_json = Column(JSONB)                              # формат shared/schema/deck_format.json

class SyncEvent(Base):
    __tablename__ = "sync_events"
    seq = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    device_id = Column(String(64), index=True)
    client_seq = Column(BigInteger, nullable=False)
    payload = Column(JSONB, nullable=False)                # attempt|session|settings|day_progress...
    created_at_ms = Column(BigInteger, nullable=False)
    __table_args__ = (Index("uq_client_event", "user_id", "device_id", "client_seq", unique=True),)

class PaceAggregate(Base):
    """Серверная копия темп-статистики — для аудита АФК и v2-дообучения SRS."""
    __tablename__ = "pace_aggregate"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    kind = Column(String(32))
    active_ms = Column(Integer)
    weight_ms = Column(Integer)
    afk_suspect = Column(Boolean, default=False)
    recorded_at_ms = Column(BigInteger)

class TtsCache(Base):
    __tablename__ = "tts_cache"
    id = Column(Integer, primary_key=True)
    lang = Column(String(16))
    text_hash = Column(String(64), index=True)
    mp3_path = Column(String(512))
    hits = Column(Integer, default=0)
