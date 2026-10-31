"""Переиспользуемые tkinter-виджеты v2: скруглённые карточки, кнопки с ховером,
прогресс-бары, тосты, пиксельный дракон на Canvas (без Pillow).

Вся логика отрисовки — здесь; app.py только дергает set_* методы.
"""
from __future__ import annotations

import time
from pathlib import Path
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


def _mascot_dir() -> Path:
    """Каталог ассетов: сначала репозиторий, затем ~/.threelanguage/assets."""
    cand = [Path(__file__).resolve().parent.parent / "assets" / "mascot",
            Path.home() / ".threelanguage" / "assets"]
    for c in cand:
        if (c / "dragon_idle.png").exists():
            return c
    return cand[0]


class Dragon(Frame):
    """Маскота из ГОТОВЫХ PNG-ассетов (assets/mascot/dragon_*.png, красный дракон).

    Ключевые исправления:
      * НЕ наследуем Canvas — Tk-баг: у подкласса Canvas внутренний tk widget
        перезаписывает атрибут ._w именем окна, и create_* падает с
        «invalid command name "<число>"» (это и был «забагованный дракон»).
      * Рисуем ТОЛЬКО ассеты; процедурный зелёный рисунок убран.
      * Pillow опционален: без него показываем пиксельную иконку-заглушку.
    """
    def __init__(self, master, *, size=T.MASCOT_SIZE):
        super().__init__(master, bg=_bg_of(master), highlightthickness=0)
        self.size = size
        self.mood = "idle"
        self._blink = False
        self._photos: dict[tuple[str, int], object] = {}
        self._keep_sprites: list = []
        self._keep: list = []
        # кропнутый по альфе спрайт (для пути B, когда ImageTk недоступен)
        self._sprite_dir = Path(__import__("tempfile").gettempdir()) / "threelanguage_sprites"
        self._sprite_dir.mkdir(exist_ok=True)
        self._img_id = None
        # фон для композита: canvas рисует поверх bg master'а — прозрачные PNG
        # пиксели должны совпадать с этим цветом, иначе на Windows/Tk8.6
        # вместо дракона остаются «осколки» предыдущего кадра (matted-артефакты)
        self._bg = _bg_of(master)
        # «Подложка»: реальный фон рисуют ЧИСТЫЕ tk.PhotoImage поверх canvas-bg.
        # На некоторых сборках Tk прозрачные пиксели put/zoom-изображений гасятся
        # в чёрный — это и был «дракон наполовину из чёрных дыр». RGB-подложка +
        # альфа-маска спрайта дают гарантированный композит без ImageTk.
        # ВАЖНО: master=корневое окно. Без него Tk привязывает образ к первому
        # корню в процессе; при переключении темы старый surface GC-ится и
        # новый PhotoImage получает ТО ЖЕ имя pyimageN — Tk рисует мусор
        # («уставший дракон», «разбросанные пиксели»).
        self._c = Canvas(self, width=size, height=size, bg=self._bg,
                         highlightthickness=0)
        self._c.pack(fill="both", expand=True)
        top = self._c.winfo_toplevel()
        self._surface = tk.PhotoImage(master=top, width=size, height=size)
        self._surface.put(self._hex_to_tk(self._bg), to=(0, 0, size, size))
        self._bg_img_id = self._c.create_image(size // 2, size // 2,
                                               image=self._surface, tags="bg")
        self._img_id = self._c.create_image(size // 2, size // 2, tags="fg")
        self.draw()

    def set_mood(self, mood: str):
        self.mood = mood
        self.draw()

    def tick(self):
        """Живость: смена настроения не нужна для мигания на PNG — просто redraw."""
        self._blink = not self._blink
        self.draw()

    def draw(self):
        # перекрасить подложку (тема могла смениться). fg НЕ сбрасываем в "":
        # ранняя очистка + упавший blit = пустой ImageTk-слот 0x0 («дракон
        # исчезает/устает» после смены настроения или тика).
        self._surface.put(self._hex_to_tk(self._bg), to=(0, 0, self.size, self.size))
        ext = getattr(self, "_external", None)
        src = None
        if ext and ("ext_img", self.size) in self._photos:
            src = self._photos[("ext_img", self.size)]
        else:
            name = MOOD_TO_PNG.get(self.mood, "dragon_idle")
            if Image is not None:
                key = (name, self.size)
                if key not in self._photos:
                    p = _mascot_dir() / f"{name}.png"
                    self._photos[key] = self._load_asset(p) if p.exists() else None
                src = self._photos.get(key)
        if src is not None:
            if self._blit_sprite(src):
                return                            # A: свой кадр; B: через surface
            self._c.itemconfigure(self._img_id, image=self._surface)
            return
        self._placeholder()                       # без Pillow/ассетов: красная заглушка

    @staticmethod
    def _hex_to_tk(hexc: str) -> str:
        """'#rrggbb' → строка для PhotoImage.put (заливка региона одним цветом)."""
        return " " + (hexc if hexc.startswith("#") else "#141a26")

    def _blit_sprite(self, img) -> bool:
        """Спрайт RGBA -> композит поверх фона темы.

        История багов «дракон наполовину из дыр / пиксели вразброс / уставший»:
          1) join(... None ...) падал TypeError на любом PNG с прозрачностью;
          2) x0 = index('#')//7 по сдвинутой строке — каждый прозрачный пиксель
             сдвигал ряд;
          3) слитный ряд '#rrggbb#rrggbb' Tcl не парсит (can't parse color) —
             между цветами ОБЯЗАТЕЛЬНЫ пробелы;
          4) ImageTk без Tk-компиляции Pillow кидает RuntimeError — ошибка
             захватывалась молча, и рисовался битый запасной путь;
          5) ГЛАВНЫЙ баг v5: PIL.Image был доступен, а PIL.ImageTk — нет
             (Pillow собран без поддержки Tk). Виджет возвращал False из blit,
             вызывающий код делал itemconfigure(image=self._surface) — то есть
             на канвасе оставалась ОДНА СПЛОШНАЯ ЗАЛИВКА ФОНOM, а сам дракон не
             рисовался никогда. Отсюда «уставший дракон» и «пустая плашка».
        Правильный ответ без ImageTk: tk.PhotoImage умеет читать PNG САМ
        (включая альфу) и красит её цветом -background — фон темы подставляется
        нативно, без ручного побайтового blit. Плюс keep-ссылка против GC.
        """
        w, h = img.size
        if w > self.size or h > self.size:
            return False
        ox, oy = (self.size - w) // 2, (self.size - h) // 2
        # путь A: ImageTk c RGBA-спрайтом НАПРЯМУЮ. Tk 8.6+ сам смешивает
        # альфу PNG с фоном canvas (-background). Раньше здесь делали RGB-
        # композит «спрайт на плашке фона» — из-за этого вокруг дракона была
        # непрозрачная тёмная рамка, не совпадающая с реальным фоном карточки
        # («уставший вид», «каша»). Убираем подложку: чистый RGBA.
        if ImageTk is not None:
            try:
                canvas_bg = str(self._c.cget("background")).lower()
                theme_hex = "#%02x%02x%02x" % self._bg_rgb()
                if canvas_bg == theme_hex:
                    # фон канваса == фон карточки: RGBA смешивает сам Tk, подложка не нужна
                    shown = img
                else:
                    # фон другой (canvas красит surface-плашку цвета темы):
                    # композитим спрайт ПОВЕРХ этой плашки, иначе альфа-поля
                    # канваса дадут «тёмную рамку вокруг дракона»
                    comp = Image.new("RGB", (self.size, self.size), self._bg_rgb())
                    comp.paste(img, (ox, oy), img)
                    shown = comp
                # ВАЖНО: keep-ссылка ДО создания PhotoImage. В этой сборке
                # Pillow ImageTk без удержания image немедленно GC-ится,
                # itemconfigure получает висячий pyimageN и канвас рисует
                # пустоту — это и был «пустой/уставший дракон» при том,
                # что _render_path честно показывал "ImageTk".
                self._keep.clear(); self._keep.append(None)
                ph = ImageTk.PhotoImage(shown)
                self._keep[-1] = ph
                self._c.coords(self._img_id, self.size // 2, self.size // 2)
                self._c.itemconfigure(self._img_id, image=ph)
                self._render_path = "ImageTk"
                return True
            except Exception as e:
                self._imagetk_error = f"{type(e).__name__}: {e}"
        else:
            self._imagetk_error = "ImageTk не импортируется (Pillow без Tk)"
        # путь B: tk.PhotoImage(file=...) — Tk сам читает PNG и сам смешивает
        # альфу с -background. Никаких put/zoom/row-shifting артефактов.
        # ВАЖНО: сначала кроп по alpha-bbox (в исходниках 256x256 дракон занимает
        # ~70% кадра; без кропа subsample даёт крошечный «уставший» спрайт).
        name = MOOD_TO_PNG.get(self.mood, "dragon_idle")
        p = _mascot_dir() / f"{name}.png"
        if getattr(self, "_external", None):
            p = Path(self._external)
        if Image is not None and img is not None:
            br, bgc, bb = self._bg_rgb()
            key = f"crop_{name}_{self.size}_{br:x}_{bgc:x}_{bb:x}"
            cropped = self._sprite_dir / f"{key}.png"
            if not cropped.exists():
                # Путь B нужен только на старых Tk без PNG-альфы: сохраняем
                # RGBA-кроп как есть — современный Tk смешает альфу сам;
                # ручную RGB-подложку цвета темы убрали (рамка «плашки»).
                img.save(cropped.with_suffix(".tmp.png"))
                import os as _os
                _os.replace(str(cropped.with_suffix(".tmp.png")), str(cropped))
            p = cropped
        if p.exists():
            try:
                ph = tk.PhotoImage(file=str(p), master=self._c.winfo_toplevel())
                m = max(ph.width(), ph.height())
                target = max(8, self.size - 4)
                if m > target:                    # целочисленный subsample
                    s = -(-m // target)
                    ph = ph.subsample(s, s)
                elif m <= target // 2:            # целочисленный zoom
                    k = max(1, target // m)
                    ph = ph.zoom(k, k)
                self._c.itemconfigure(self._img_id, image=ph)
                self._keep.append(ph)
                self._render_path = "tk.PhotoImage"
                return True
            except Exception as e:
                self._photo_load_error = f"{type(e).__name__}: {e}"
        return False

    def _placeholder(self):
        """Без рабочего ImageTk: грузим PNG НАПРЯМУЮ через tk.PhotoImage —
        Tk сам читает RGBA и свою альфу, никаких put/zoom-артефактов.
        Это замена прежнему ручному побайтовому blit, который и рисовал
        «разбросанные полупиксели»."""
        name = MOOD_TO_PNG.get(self.mood, "dragon_idle")
        key = ("tk_" + name, self.size)
        if key not in self._photos:
            p = _mascot_dir() / f"{name}.png"
            ph = None
            if p.exists():
                try:
                    ph = tk.PhotoImage(file=str(p))
                    # целочисленный zoom/subsample только по максимуму стороны
                    m = max(ph.width(), ph.height())
                    target = max(8, self.size - 4)
                    if m <= target // 2:
                        k = max(1, target // m)
                        ph = ph.zoom(k, k)
                    elif m > target:
                        s = -(-m // target)
                        ph = ph.subsample(s, s)
                except Exception:
                    ph = None
            self._photos[key] = ph
        ph = self._photos.get(key)
        if ph is not None:
            self._c.itemconfigure(self._img_id, image=ph)
            return
        # совсем без ассетов: красная пиксельная рыбка-заглушка (не зелёная!)
        c = self._c
        red, dark = "#d02828", "#8c1a20"
        body = [(x, y) for y in range(4, 9) for x in range(3, 12)]
        for (x, y) in body:
            c.create_rectangle(x * 8, y * 8, x * 8 + 8, y * 8 + 8,
                               fill=red, outline="", tags="sprite")
        c.create_rectangle(11 * 8, 5 * 8, 13 * 8, 7 * 8, fill=dark, outline="", tags="sprite")
        c.create_oval(4 * 8, 5 * 8, 5 * 8 + 4, 6 * 8, fill="#fff", outline="", tags="sprite")

    def _bg_rgb(self):
        # hex -> напрямую; имя цвета Tk ('gray', 'systemButtonFace') -> winfo_rgb,
        # на битом имени не падаем — берём тёмно-синий фон темы
        b = (self._bg or "").lstrip("#")
        if len(b) == 6:
            try:
                return (int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16))
            except ValueError:
                pass
        try:
            rgb = self._c.winfo_toplevel().winfo_rgb(self._bg)
            return tuple(c >> 8 for c in rgb)
        except Exception:
            return (0x14, 0x1A, 0x26)

    BLOCK = 4  # ассеты — пиксель-арт с блоком 4x4 px

    @staticmethod
    def _detect_block(img) -> int:
        """Найти размер пиксель-блока по горизонтали: минимальный b>=1, при
        котором все строки состоят только из константных прогонов длиной k*b.
        Фолбэк — BLOCK. Это защищает от «каши», если ассет пере-масштабирован
        и его сетка больше не кратна 4."""
        w, h = img.size
        data = img.convert("RGB")
        px = data.load()
        import math
        g = 0
        for y in range(0, h, max(1, h // 32)):     # выборочные строки
            run = 1
            for x in range(1, w):
                if px[x, y] == px[x - 1, y]:
                    run += 1
                else:
                    g = math.gcd(g, run); run = 1
            g = math.gcd(g, run)
            if g == 1:
                break
        return g if g >= 1 else 0

    def _load_asset(self, path):
        """PNG → PIL RGBA в габарит виджета. ТОЛЬКО целочисленные шаги сетки.

        Корень бага «красная каша / половина пикселей вразброс»: getbbox()
        даёт координаты вроде (36,42)-(219,213), не кратные 4px-сетке блоков,
        а NEAREST-resize в произвольный размер режет блоки пополам — цвета
        перемешиваются. Правильный конвейер:
          1) кроп bbox, расширенный ДО ГРАНИЦ блоков 4x4 (фаза сетки сохранена);
          2) даунскейл строго каждый блок 4x4 -> 1 px (мажоритарный цвет
             непрозрачных пикселей блока) — получаем честную пиксельную сетку;
          3) апскейл только целым коэффициентом NEAREST.
        Никаких дробных subsample/resize."""
        img = Image.open(path).convert("RGBA")
        B = self._detect_block(img) or self.BLOCK
        bbox = img.getbbox()
        if not bbox:
            return None
        x0, y0, x1, y1 = bbox
        # выравниваем кроп по сетке блоков, не вылезая за холст
        x0 = max(0, (x0 // B) * B)
        y0 = max(0, (y0 // B) * B)
        x1 = min(img.width, ((x1 + B - 1) // B) * B)
        y1 = min(img.height, ((y1 + B - 1) // B) * B)
        crop = img.crop((x0, y0, x1, y1))
        gw, gh = crop.width // B, crop.height // B
        if gw < 1 or gh < 1:
            return None
        from collections import Counter
        ca = list(crop.getdata())
        cw = crop.width
        out = []
        for gy in range(gh):
            row_off = gy * B * cw
            for gx in range(gw):
                blk = [ca[row_off + (dy * cw) + gx * B + dx]
                       for dy in range(B) for dx in range(B)]
                op = [p[:3] for p in blk if p[3] > 10]
                if op:
                    col = Counter(op).most_common(1)[0][0]
                    out.append(col + (255,))
                else:
                    out.append((0, 0, 0, 0))
        grid = Image.new("RGBA", (gw, gh))
        grid.putdata(out)
        # апскейл только целым числом: вписываемся в target по большей стороне
        target = max(B, self.size - 4)
        k = max(1, target // max(gw, gh))
        big = grid.resize((gw * k, gh * k), Image.NEAREST)
        if max(big.size) > self.size:              # единичный случай guard
            return None
        return big

    def _via_imagetk(self, img):
        """Fallback: ImageTk (только если Pillow собран с Tk-поддержкой)."""
        if ImageTk is None:
            return None
        try:
            size = self.size
            comp = Image.new("RGB", (size, size), self._bg_rgb())
            ox, oy = (size - img.width) // 2, (size - img.height) // 2
            comp.paste(img, (ox, oy), img)
            return ImageTk.PhotoImage(comp)
        except Exception:
            return None

    def load_external(self, path) -> bool:
        """Загрузить внешний PNG (drag&drop) вместо ассета настроения."""
        try:
            if Image is not None:
                self._photos[("ext_img", self.size)] = self._load_asset(path)
            else:
                self._photos[("ext_img", self.size)] = None
                self._photos[("ext", self.size)] = tk.PhotoImage(file=str(path))
                self._c.delete("sprite")
                self._c.create_image(self.size // 2, self.size // 2,
                                     image=self._photos[("ext", self.size)],
                                     tags="sprite")
            self._external = str(path)
            self.draw()
            return True
        except Exception:
            return False

    def _placeholder(self):
        self._c.delete("sprite")
        """Мини-заглушка без Pillow/ассетов: красный силуэт (не зелёный!),
        отцентрован в квадрате size x size."""
        px = max(4, self.size // 16)
        ox = oy = (self.size - 16 * px) // 2
        red, dark = "#d63b3b", "#8f2020"
        cells = [(4, 2), (7, 1), (10, 2),                      # три головы
                 (5, 4), (6, 4), (7, 4), (8, 4), (9, 4),       # шеи
                 (4, 6), (5, 6), (6, 6), (7, 6), (8, 6), (9, 6), (10, 6),
                 (4, 7), (5, 7), (6, 7), (7, 7), (8, 7), (9, 7), (10, 7),
                 (11, 7), (12, 6), (13, 5)]                     # хвост
        for ix, iy in cells:
            self._c.create_rectangle(ox + ix * px, oy + iy * px,
                                     ox + (ix + 1) * px, oy + (iy + 1) * px,
                                     fill=red if (ix + iy) % 3 else dark, outline="")
