"""Тема оформления v2: тёмный «пиксель-драконий» стиль + дизайн-токены.

Все цвета/шрифты/отступы — только отсюда, чтобы UI можно было перекрасить
в один место. Никакой логики здесь нет — чисто визуальный слой.
"""
from __future__ import annotations

# ---- палитры: тёмная по умолчанию + светлая ------------------------------
THEMES = {
    "dark": dict(BG="#141a26", PANEL="#1d2534", PANEL_HI="#263145", TEXT="#e8edf5",
                 TEXT_DIM="#93a1b5", ACCENT="#ffb638", GREEN="#3ecf8e", RED="#ff6b6b",
                 BLUE="#5aa7ff", BORDER="#33415a"),
    "light": dict(BG="#f2f4f8", PANEL="#ffffff", PANEL_HI="#e3e8f0", TEXT="#1c2430",
                  TEXT_DIM="#5d6b80", ACCENT="#e09a17", GREEN="#1f9d63", RED="#d64545",
                  BLUE="#2f6fd0", BORDER="#c3ccda"),
}

BG = PANEL = PANEL_HI = TEXT = TEXT_DIM = ACCENT = GREEN = RED = BLUE = BORDER = ""


def set_theme(name: str):
    """Переключает модульные цвета; вызывается ДО построения UI и при смене темы."""
    global BG, PANEL, PANEL_HI, TEXT, TEXT_DIM, ACCENT, GREEN, RED, BLUE, BORDER
    p = THEMES.get(name, THEMES["dark"])
    g = globals()
    g.update(p)

LANG_COLORS = {"en": "#5aa7ff", "pt-BR": "#3ecf8e", "zh": "#ff6b6b"}

# ---- шрифты ---------------------------------------------------------------
def font(size: int, weight: str = "normal", family: str | None = None) -> tuple:
    fam = family or "TkDefaultFont"
    return (fam, size, weight)

FS_HERO   = font(26, "bold")
FS_TITLE  = font(16, "bold")
FS_PROMPT = font(18)
FS_BODY   = font(11)
FS_SMALL  = font(9)
FS_BTN    = font(12, "bold")
FS_STAT   = font(10)

# ---- размеры --------------------------------------------------------------
WIN_W, WIN_H   = 980, 680
MIN_W, MIN_H   = 760, 560
PAD            = 16
CARD_PAD       = 14
RADIUS         = 12          # для canvas-скруглений
MASCOT_SIZE    = 96
BAR_LEN        = 170

# ---- тексты ---------------------------------------------------------------
TXT = {
    "correct": "Верно! 🎉",
    "partial": "Почти! Мелкая опечатка прощена.",
    "wrong":   "Неверно. Карточка вернётся быстрее.",
    "afk":     "Слишком медленно — темп-монитор списывает это как АФК.",
    "sleep":   "Дракон уснул… Пауза не учитывается.",
    "welcome": "Три языка, один дракон.",
}


set_theme("dark")
