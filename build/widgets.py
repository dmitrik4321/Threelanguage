"""Переиспользуемые tkinter-виджеты v2: скруглённые карточки, кнопки с ховером,
прогресс-бары, тосты, пиксельный дракон на Canvas (без Pillow).

Вся логика отрисовки — здесь; app.py только дергает set_* методы.
"""
from __future__ import annotations

import time
import tkinter as tk
from tkinter import Canvas, Frame, Label

import theme as T

try:
    from PIL import Image, ImageTk
except Exception:
    Image = ImageTk = None


# ------------------------------------------------------------------ utils
def round_rect(c: Canvas, x1, y1, x2, y2, r, *, fill="", outline="", tags=()):
    """Скруглённый прямоугольник из дуг+линий.

    ВАЖНО: координаты передаём ОДНИМ КОРОТКИМ СПИСКОМ — tkinter сам разворачивает
    list/tuple в позиционные args для canvas-items. Позиционный *args-сплинтинг
    ломает обработку опциональных аргументов tkinter и приводит к «invalid command
    name "<число>"» (число уезжает в имя окна)."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, fill=fill, outline=outline, tags=tags)


def _bg_of(w):
    """Фон контейнера; если у виджета ещё нет собственного bg — берём у корня."""
    try:
        v = w.cget("bg")
        if isinstance(v, str) and v.startswith("#"):
            return v
    except Exception:
        pass
    return T.BG


class Card(Frame):
    """Панель-карточка с фоном темы. Только keyword-параметры (Frame-безопасно)."""
    def __init__(self, master, **kw):
        bg = kw.pop("bg", T.PANEL)
        super().__init__(master, bg=bg, highlightbackground=T.BORDER,
                         highlightthickness=1, **kw)


class PillButton(Frame):
    """Кнопка-«таблетка» со скруглением и ховером. Композиция: Frame + Canvas
    (Canvas живёт ВНУТРИ фрейма; сам виджет не наследует TkinterWidget — так
    исключён баг «invalid command name <id>» при create_* на self)."""
    def __init__(self, master, text, *, command=None, width=240, height=44,
                 color=T.PANEL_HI, hover=T.BORDER, fg=T.TEXT, font=T.FS_BTN,
                 accent=None):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0)
        self._cmd, self._enabled = command, True
        self._bar_w, self._bar_h = width, height
        self._colors = (color, hover)
        self._cur = color
        self._c = Canvas(self, width=width, height=height, bg=_bg_of(master),
                         highlightthickness=0, cursor="hand2")
        self._c.pack(fill="both", expand=True)
        round_rect(self._c, 1, 1, width - 1, height - 1, height // 2,
                   fill=color, outline="", tags="bgall")
        if accent:
            self._c.create_rectangle(6, 10, 10, height - 10, fill=accent, outline="")
        self._txt = self._c.create_text(width // 2 + (8 if accent else 0), height // 2,
                                        text=text, fill=fg, font=font)
        for w in (self, self._c):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click)

    def configure_text(self, text):
        self._c.itemconfigure(self._txt, text=text)

    def set_text_color(self, color: str):
        self._c.itemconfigure(self._txt, fill=color)

    def set_enabled(self, on: bool):
        self._enabled = on
        self._c.configure(cursor="arrow" if not on else "hand2")
        self._redraw(T.PANEL if not on else self._cur)
        self._c.itemconfigure(self._txt, fill=T.TEXT_DIM if not on else T.TEXT)

    def _redraw(self, color):
        self._c.delete("bgall")
        round_rect(self._c, 1, 1, self._bar_w - 1, self._bar_h - 1, self._bar_h // 2,
                   fill=color, outline="", tags="bgall")
        self._c.tag_lower("bgall")

    def _on_enter(self, _):
        if self._enabled:
            self._redraw(self._colors[1])

    def _on_leave(self, _):
        if self._enabled:
            self._redraw(self._colors[0])

    def _on_click(self, _):
        if self._enabled and self._cmd:
            self._cmd()


class LangBar(Frame):
    """Тонкий прогресс-бар (день по языку / прогресс экрана). Композиция:
    Frame + внутренний Canvas — без наследования Canvas (см. PillButton)."""
    def __init__(self, master, *, lang=None, width=T.BAR_LEN, height=10, **kw):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0, **kw)
        self._c = Canvas(self, width=width, height=height + 2, bg=_bg_of(master),
                         highlightthickness=0)
        self._c.pack()
        self._bar_w, self._bar_h = width, height
        self.color = T.LANG_COLORS.get(lang, T.ACCENT)
        round_rect(self._c, 1, 1, width - 1, height, height // 2,
                   fill=T.BG, outline=T.BORDER)
        self._fill = round_rect(self._c, 1, 1, 2, height, height // 2,
                                fill=self.color, outline="")
        self.set(0.0)

    def set(self, frac: float):
        frac = max(0.0, min(1.0, frac))
        w = max(2, int(self._bar_w * frac))
        self._c.coords(self._fill, 1, 1, w, self._bar_h)


class Toast(Frame):
    """Полупрозрачный не возможен в tk — имитация: панель внизу, автоскрытие."""
    def __init__(self, master):
        super().__init__(master, bg=T.PANEL, highlightbackground=T.BORDER, highlightthickness=1)
        self._lbl = Label(self, text="", bg=T.PANEL, fg=T.TEXT, font=T.FS_STAT)
        self._lbl.pack(padx=14, pady=6)
        self._job = None
        self._hide_job = None

    def show(self, text: str, color: str | None = None, ms: int = 2600):
        self._lbl.config(text=text, fg=color or T.TEXT)
        if not self.winfo_ismapped():
            self.pack(side="bottom", fill="x", padx=T.PAD, pady=(0, T.PAD))
        if self._hide_job:
            self.after_cancel(self._hide_job)
        self._hide_job = self.after(ms, self._hide)

    def _hide(self):
        self.pack_forget()
        self._hide_job = None


# ------------------------------------------------------------------ mascot
MOOD_TO_PNG = {"idle": "dragon_idle", "cheer": "dragon_cheer", "sad": "dragon_sad",
               "angry": "dragon_angry", "sleep": "dragon_sleep"}


class Dragon(Canvas):
    """Маскота: PNG через Pillow, иначе процедурный пиксельный дракон (3 головы)."""
    PIXEL = 8

    def __init__(self, master, *, size=T.MASCOT_SIZE):
        super().__init__(master, width=size, height=size,
                         bg=_bg_of(master), highlightthickness=0)
        self.size = size
        self.mood = "idle"
        self._photos = {}
        self._blink = False
        self.draw()

    def set_mood(self, mood: str):
        self.mood = mood
        self.draw()

    def tick(self):
        """Мигание раз в ~3с — живой экран без нагрузки."""
        self._blink = not self._blink
        if self.mood != "sleep":
            self.draw()

    # ---- PNG путь
    def _try_png(self, name: str) -> bool:
        if Image is None:
            return False
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "assets" / "mascot" / f"{name}.png"
        if not p.exists():
            return False
        key = (name, self.size)
        if key not in self._photos:
            img = Image.open(p).convert("RGBA").resize((self.size - 4, self.size - 4), Image.NEAREST)
            self._photos[key] = ImageTk.PhotoImage(img)
        self.create_image(self.size // 2, self.size // 2, image=self._photos[key])
        return True

    # ---- процедурный пиксельный дракон
    def draw(self):
        self.delete("all")
        s = self.size
        if self._try_png(MOOD_TO_PNG.get(self.mood, "dragon_idle")):
            return
        px = s // 24                      # сетка 24×24 «пикселя»
        cx = lambda i: (i * px, (i + 1) * px)

        def sq(ix, iy, color):
            x1, x2 = cx(ix); y1, y2 = cx(iy)
            self.create_rectangle(x1, y1, x2, y2, fill=color, outline="")

        body, belly = "#2e9e6b", "#7fe0a8"
        head_c = {"idle": body, "cheer": "#3ecf8e", "sad": "#5b6ee1",
                  "angry": "#e15b5b", "sleep": "#3a4a63"}[self.mood]
        eye = "#f5efe6" if self.mood != "sleep" else "#93a1b5"
        # тело
        for ix in range(7, 17):
            for iy in range(14, 20):
                sq(ix, iy, belly if iy >= 18 else body)
        # хвост
        for i in range(4):
            sq(17 + i, 17 - i // 2, body)
        # три головы (центр/лево/право)
        heads = [(5, 4), (11, 2), (17, 4)]
        for hx, hy in heads:
            for ix in range(hx, hx + 4):
                for iy in range(hy, hy + 4):
                    sq(ix, iy, head_c)
            # морда
            sq(hx + 4, hy + 2, head_c)
            sq(hx + 4, hy + 3, head_c)
            # глаз (мигание = закрыт)
            if self._blink or self.mood == "sleep":
                sq(hx + 2, hy + 1, head_c)
                sq(hx + 2, hy + 2, eye)
            else:
                sq(hx + 2, hy + 1, eye)
                sq(hx + 2, hy + 2, "#141a26")
            # рог
            sq(hx + 1, hy - 1, T.ACCENT)
        # ноздри-акцент при cheer — огонёк
        if self.mood == "cheer":
            for ix in range(21, 23):
                sq(ix, 5, T.ACCENT)
        if self.mood == "sleep":
            for (ix, iy) in [(20, 3), (21, 2), (22, 1)]:
                sq(ix, iy, "#93a1b5")   # Z-z-z
