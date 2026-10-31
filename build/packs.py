"""Сканер языковых пакетов (lang_packs/).

Два типа пакетов:
  * app_ui — язык интерфейса приложения (папки lang_packs/app_ui/<код>/pack.json);
  * study  — язык для изучения «из» родного (папки lang_packs/study/<src>-<tgt>/pack.json,
             формат «английский из русского» = ru-en).

Приложение сканирует обе директории при старте и на кнопке «Обновить список»
в настройках; недоступные/битые пакеты помечаются, но не роняют UI.
"""
from __future__ import annotations

import json
from pathlib import Path

PACKS_ROOT = Path(__file__).resolve().parent.parent / "lang_packs"


def _load(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and d.get("type") in ("app_ui", "study"):
            d["_path"] = str(p)
            return d
    except Exception:
        return None
    return None


def scan_app_ui() -> list[dict]:
    out = []
    root = PACKS_ROOT / "app_ui"
    if root.is_dir():
        for sub in sorted(root.iterdir()):
            f = sub / "pack.json"
            if f.is_file():
                d = _load(f)
                if d and d["type"] == "app_ui":
                    out.append(d)
    return out


def scan_study() -> list[dict]:
    out = []
    root = PACKS_ROOT / "study"
    if root.is_dir():
        for sub in sorted(root.iterdir()):
            f = sub / "pack.json"
            if f.is_file():
                d = _load(f)
                if d and d["type"] == "study":
                    out.append(d)
    return out


def strings_for(code: str) -> dict:
    """Строки интерфейса для языка кода; пусто = fallback на встроенные."""
    for p in scan_app_ui():
        if p.get("code") == code:
            return dict(p.get("strings", {}))
    return {}
