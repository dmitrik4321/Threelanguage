"""Threelanguage — Python-билд десктоп-приложения (tkinter), UI v2.

Запуск:  python3 build/app.py     (без сборки EXE, нужен только Python 3.10+)

UI v2 (по фидбеку «figня»):
  * Экранная архитектура: home → placement → lesson → results; боковой дашборд
    с драконом, днями и минутами по языкам; тосты вместо мигающего статуса.
  * Тёмная тема + дизайн-токены (theme.py), переиспользуемые виджеты
    (widgets.py): скруглённые кнопки с ховером, прогресс-бары по языкам,
    процедурный пиксельный дракон (PNG через Pillow, если есть).
  * Горячие клавиши: 1..4 — варианты ответа, Enter — проверка ввода,
    Esc — пауза/домой, пробел на экране результатов — следующий урок.

Ядро НЕ дублируется — импортируется из shared/algorithm/reference.py
(SRS FSRS-lite, AFK-темп-монитор ×10 медианы, оценка урока 0..100,
placement IRT, partial_credit NFKC+Levenshtein для CJK). Состояние — SQLite
в ~/.threelanguage/desktop.db. Аудио/говорение — заглушки этого билда.
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import Entry, Frame, Label, Tk, ttk
try:                                     # опционально: перетаскивание PNG-маскоты
    from tkinterdnd2 import TkinterDnD   # pip install tkinterdnd2
except Exception:
    TkinterDnD = None

ROOT = Path(__file__).resolve().parent.parent          # корень репозитория
sys.path.insert(0, str(ROOT / "shared" / "algorithm"))  # эталон ядра
sys.path.insert(0, str(Path(__file__).resolve().parent))

import reference as core                                 # noqa: E402
import theme as T                                        # noqa: E402

TXT_RU = {
    "correct": "Верно! 🎉",
    "partial": "Почти! Мелкая опечатка прощена.",
    "wrong":   "Неверно. Карточка вернётся быстрее.",
    "afk":     "Слишком медленно — темп-монитор списывает это как АФК.",
    "sleep":   "Дракон уснул… Пауза не учитывается.",
    "welcome": "Три языка, один дракон.",
}
from content import LESSON, PLACEMENT_ITEMS              # noqa: E402
import data_sources as ds                                 # noqa: E402  живые API (Tatoeba/Wiktionary)
from widgets import (Card, Dragon, LangBar, MOOD_TO_PNG, PillButton,
                     Toast)  # noqa: E402
import packs as packscan                            # noqa: E402
from sync_client import SyncClient                   # noqa: E402

DB_PATH = Path.home() / ".threelanguage" / "desktop.db"
LANG_NAMES = {"en": "English", "pt-BR": "Português", "zh": "中文"}
DAILY_GO_MIN = 3           # минут на язык за день
IDLE_DIM_MS = 90_000       # docs/AFK.md: idle > 90с → сонный дракон
FEEDBACK_MS = 850          # пауза показа вердикта между заданиями


# ---------------------------------------------------------------- persistence
def load_state() -> dict:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    row = con.execute("SELECT v FROM kv WHERE k='state'").fetchone()
    st = json.loads(row[0]) if row else default_state()
    con.close()
    return st


def save_state(st: dict):
    con = sqlite3.connect(DB_PATH)
    con.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT OR REPLACE INTO kv VALUES ('state', ?)",
                (json.dumps(st, ensure_ascii=False),))
    con.commit()
    con.close()


def default_state() -> dict:
    return {
        "day": time.strftime("%Y-%m-%d"),
        "cards": {},                                  # card_id -> CardState dict
        "pace": {"samples": {}},                      # PaceStats
        "minutes": {l: 0.0 for l in LANG_NAMES},      # effective ms по языкам
        "history": [],                                # [{lesson, score, ts, n}]
        "placement_done": False,
        "level": None,
        "settings": {"theme": "dark", "ui_lang": "ru"},
        "account": {"username": None, "token": None},
        # Реестр слов для UserData-журнала сервера (см. local_server/server.py):
        # card_id -> {text, lang, source, license}. Клиент докладывает данные
        # каждой показанной карточки при push — так сервер знает, ЧТО за слово
        # стоит за хэшем id, без обращения к внешним API.
        "words": {},
    }


def ensure_settings(st: dict) -> dict:
    st.setdefault("settings", {"theme": "dark", "ui_lang": "ru"})
    st["settings"].setdefault("theme", "dark")
    st["settings"].setdefault("ui_lang", "ru")
    st.setdefault("account", {"username": None, "token": None})
    return st


def roll_day(st: dict):
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["minutes"] = {l: 0.0 for l in LANG_NAMES}


# ---------------------------------------------------------------- app
class App:
    def __init__(self, tk_root: Tk):
        self.tk = tk_root
        tk_root.title("Threelanguage")
        tk_root.geometry(f"{T.WIN_W}x{T.WIN_H}")
        tk_root.minsize(T.MIN_W, T.MIN_H)
        tk_root.configure(bg=T.BG)

        self.st = ensure_settings(load_state())
        roll_day(self.st)
        self.pace = core.PaceStats(**self.st["pace"])

        # ---- тема/локаль из настроек (до построения UI)
        self.strings: dict = {}
        self.sync = SyncClient()
        acct = self.st.get("account", {})
        if acct.get("token"):
            self.sync.token, self.sync.username = acct["token"], acct.get("username")
        self.apply_theme(self.st["settings"]["theme"])
        self.load_strings(self.st["settings"]["ui_lang"])

        # ---- layout: сайдбар | контент, снизу тост
        self.sidebar = self._build_sidebar(tk_root)
        self.sidebar.pack(side="left", fill="y", padx=(T.PAD, 0), pady=T.PAD)
        self.content = Frame(tk_root, bg=T.BG)
        self.content.pack(side="left", fill="both", expand=True,
                          padx=T.PAD, pady=T.PAD)
        self.toast = Toast(tk_root)

        # ---- runtime
        self.mode = None            # 'home' | 'placement' | 'lesson' | 'done'
        self.task_events: list[tuple[int, str]] = []
        self.task_start_wall = 0.0
        self.last_input_t = 0.0
        self.current_task = None
        self.session_answers: list[core.AnswerRecord] = []
        self.lang_of_current = "en"
        self._pl_idx = 0
        self._pl_responses: list[float] = []
        self._dimmed = False
        self._lock = False          # блокировка кнопок на время фидбека
        self._opt_buttons: list[PillButton] = []
        self.entry = None

        tk_root.bind("<Escape>", lambda e: self.go_home())
        tk_root.protocol("WM_DELETE_WINDOW", self.on_close)
        # KeyPress надёжнее KeyRelease+keysym Digit1 (Digit1 отсутствует в старых X keysym)
        for d in "1234":
            tk_root.bind(f"<KeyPress-{d}>", lambda e, i=int(d) - 1: self._hotkey(i))

        # ---- живой контент из свободных API (Tatoeba/Wiktionary), без блокировки UI.
        # LESSON/PLACEMENT_ITEMS остаются офлайн-заглушками: если сеть недоступна
        # или fetch ещё не завершился, урок строится из них (см. _current_lesson).
        self.lesson_live = dict(LESSON)          # активная колода (fallback + live)
        self.placement_live = list(PLACEMENT_ITEMS)
        self._content_loading = False
        tk_root.after(400, self.prefetch_content)

        self.update_dashboard()
        self.show_home()
        self.schedule_idle_check()
        self.schedule_blink()

    # ------------------------------------------------------ живой контент
    def prefetch_content(self):
        """Грузит уроки/placement из API в фоне; по готовности обновляет колоды.
        Сетевые ошибки молча оставляют заглушки — приложение всегда рабочее."""
        if self._content_loading:
            return
        self._content_loading = True
        import threading
        langs = list(LANG_NAMES)

        def worker():
            try:
                lesson = ds.build_lesson(langs)
                items = ds.placement_items(["pt-BR"])
            except Exception:
                lesson, items = None, None
            # возврат в UI-поток через after_idle (tkinter не тредобезопасен)
            self.tk.after(0, lambda: self._content_ready(lesson, items))

        threading.Thread(target=worker, daemon=True).start()

    def _content_ready(self, lesson, items):
        self._content_loading = False
        changed = False
        if lesson and lesson.get("tasks"):
            self.lesson_live = lesson
            changed = True
        if items:
            self.placement_live = items
        if changed and self.mode == "home" and not self.st["placement_done"]:
            self.toast.show("Контент обновлён из Tatoeba (CC BY)", T.GREEN, ms=2500)

    # ------------------------------------------------------ тема / локаль
    def apply_theme(self, name: str):
        T.set_theme(name)
        try:
            self.tk.configure(bg=T.BG)
        except Exception:
            pass

    def load_strings(self, code: str):
        """Строки из языкового пакета app_ui; встроенный русский — fallback."""
        base = dict(TXT_RU)
        base.update(packscan.strings_for(code))
        self.strings = base
        T.TXT = base

    def tr(self, key: str) -> str:
        return self.strings.get(key, TXT_RU.get(key, key))

    # ------------------------------------------------------ sidebar
    def _build_sidebar(self, root):
        sb = Card(root)
        sb.configure(width=250)
        sb.pack_propagate(False)
        inner = Frame(sb, bg=T.PANEL)
        inner.pack(fill="both", expand=True, padx=T.CARD_PAD, pady=T.CARD_PAD)

        Label(inner, text="🐉 Threelanguage", bg=T.PANEL, fg=T.ACCENT,
              font=T.font(15, "bold")).pack(anchor="w")
        Label(inner, text=self.tr("welcome"), bg=T.PANEL, fg=T.TEXT_DIM,
              font=T.FS_SMALL).pack(anchor="w", pady=(0, 10))

        self.dragon = Dragon(inner, size=120)
        self.dragon.pack(pady=(0, 12))
        self._dragon_dnd = hasattr(self.tk, 'register')  # tkdnd доступен?
        if self._dragon_dnd:
            try:
                self.dragon.register('*')
                self.dragon.drop_target_register('*')
                self.dragon.dnd_bind('<<Drop>>', self._on_mascot_drop)
            except Exception:
                self._dragon_dnd = False

        self.level_lbl = Label(inner, text="", bg=T.PANEL, fg=T.TEXT, font=T.FS_BODY)
        self.level_lbl.pack(anchor="w", pady=(0, 10))

        Label(inner, text="Сегодня по языкам", bg=T.PANEL, fg=T.TEXT_DIM,
              font=T.FS_SMALL).pack(anchor="w")
        self.bars: dict[str, tuple[LangBar, Label]] = {}
        for lang in LANG_NAMES:
            row = Frame(inner, bg=T.PANEL)
            row.pack(fill="x", pady=4)
            bar = LangBar(row, lang=lang)
            bar.pack(side="left")
            lbl = Label(row, text="0.0", bg=T.PANEL, fg=T.LANG_COLORS[lang],
                        font=T.FS_SMALL, width=6)
            lbl.pack(side="right")
            self.bars[lang] = (bar, lbl)

        self.streak_lbl = Label(inner, text="", bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL)
        self.streak_lbl.pack(anchor="w", pady=(14, 0))
        return sb

    def _on_mascot_drop(self, event):
        """PNG-маскоту можно перетащить на дракона — смена настроения по имени."""
        from pathlib import Path as P
        p = P(event.data.strip("{} "))
        if p.suffix.lower() == ".png":
            stem = p.stem
            for mood, name in MOOD_TO_PNG.items():
                if name in stem:
                    self.dragon.set_mood(mood)
                    break
            self.dragon.load_external(p)
            self.toast.show(f"Маскота: {p.name}", T.GREEN)

    def update_dashboard(self):
        roll_day(self.st)
        for lang, (bar, lbl) in self.bars.items():
            m = self.st["minutes"].get(lang, 0.0)
            bar.set(m / DAILY_GO_MIN)
            lbl.config(text=f"{m:.1f} мин")
        lvl = self.st.get("level")
        self.level_lbl.config(
            text=f"Уровень: {lvl}" if lvl else "Уровень: не определён")
        done = len(self.st["history"])
        best = max((h["score"] for h in self.st["history"]), default=None)
        self.streak_lbl.config(
            text=f"Уроков пройдено: {done}" + (f" · лучший балл: {best}" if best is not None else ""))

    # ------------------------------------------------------ screens
    def clear_content(self):
        for w in self.content.winfo_children():
            w.destroy()
        self._opt_buttons, self.entry = [], None

    def go_home(self):
        if self.mode == "lesson" and self.session_answers:
            self.save_now()
        self._lock = False
        self.show_home()

    def show_home(self):
        self.mode = "home"
        self.clear_content()
        self.dragon.set_mood("idle")
        c = Card(self.content)
        c.pack(fill="both", expand=True)
        inner = Frame(c, bg=T.PANEL)
        inner.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.8, relheight=0.8)

        Label(inner, text="Три языка. Один дракон.", bg=T.PANEL, fg=T.TEXT,
              font=T.FS_HERO).pack(pady=(30, 6))
        Label(inner, text="EN · pt-BR · zh — один урок тренирует все три,\n"
                          "интервалы повторов считает SRS, темп следит АФК-монитор.",
              bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_BODY, justify="left").pack(pady=(0, 24))

        if self.st["placement_done"]:
            PillButton(inner, self.tr("start_lesson"), command=self.start_lesson,
                       width=280, height=52, color=T.GREEN, hover="#54e0a4",
                       fg="#0d1b14").pack(pady=5)
            PillButton(inner, "Пройти placement заново", command=self.start_placement,
                       width=280).pack(pady=5)
        else:
            PillButton(inner, "▶  Placement-тест (~2 минуты)", command=self.start_placement,
                       width=280, height=52, color=T.ACCENT, hover="#ffc866",
                       fg="#241a05").pack(pady=5)
        row = Frame(inner, bg=T.PANEL); row.pack(pady=(10, 0))
        PillButton(row, "⚙  Настройки", command=self.show_settings, width=170,
                   height=38).pack(side="left", padx=4)
        acct_lbl = Label(inner, text=("👤 " + str(self.sync.username)) if self.sync.token
                         else "Аккаунт: локальный режим (вход в Настройках)",
                         bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL)
        acct_lbl.pack(pady=(12, 0))
        hist = self.st["history"][-3:]
        if hist:
            txt = "Последние: " + " · ".join(f"{h['score']}/100" for h in hist)
            Label(inner, text=txt, bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL).pack(pady=(18, 0))
        Label(inner, text="Esc — домой · 1..4 — выбор варианта · Enter — проверить",
              bg=T.PANEL, fg=T.BORDER, font=T.FS_SMALL).pack(side="bottom", pady=(0, 8))

    # ------------------------------------------------------ idle / AFK watchdog
    def schedule_idle_check(self):
        self._hb_job = None
        self._heartbeat()
        self._idle_job = self.tk.after(2000, self._idle_tick)

    def _idle_tick(self):
        if self.mode == "lesson" and not self._dimmed:
            idle = (time.time() - self.last_input_t) * 1000
            if idle > IDLE_DIM_MS and self.task_events:
                self._dimmed = True
                self.task_events.append((int(idle), "focus_out"))
                self.dragon.set_mood("sleep")
                self.toast.show(self.tr("sleep"), T.BLUE, ms=4000)
        self._idle_job = self.tk.after(2000, self._idle_tick)

    def schedule_blink(self):
        self.dragon.tick()
        self.tk.after(3000, self.schedule_blink)

    def on_input(self):
        now_rel = int((time.time() - self.task_start_wall) * 1000)
        if self._dimmed:
            self._dimmed = False
            self.task_events.append((now_rel, "focus_in"))
            self.dragon.set_mood("idle")
        self.task_events.append((now_rel, "input"))
        self.last_input_t = time.time()

    def _entry_row(self, parent, *, placeholder=""):
        """Поле ввода в рамке-карточке: кликабельно (клик по рамке фокусит поле),
        видимый border, курсор I-beam. Возвращает Entry."""
        box = Frame(parent, bg=T.BG, highlightbackground=T.BORDER, highlightthickness=2,
                    cursor="xterm")
        box.pack(padx=40, pady=6, fill="x")
        e = Entry(box, font=T.FS_TITLE, bg=T.BG, fg=T.TEXT,
                  insertbackground=T.TEXT, relief="flat", bd=0,
                  disabledbackground=T.PANEL)
        e.pack(fill="x", padx=12, pady=9)
        box.bind("<Button-1>", lambda ev: (e.focus_force(),))
        if placeholder:
            def _ph_show(_=None):
                if not e.get():
                    e.insert(0, placeholder)
                    e.config(fg=T.TEXT_DIM)
            def _ph_hide(_=None):
                if e.get() == placeholder:
                    e.delete(0, "end"); e.config(fg=T.TEXT)
            e.bind("<FocusIn>", _ph_hide)
            e.bind("<FocusOut>", _ph_show)
            e.bind("<KeyRelease>", lambda ev: None)  # no-op, порядок биндов важен
            _ph_show()
        return e

    # ------------------------------------------------------ placement
    def start_placement(self):
        self.mode = "placement"
        self._pl_idx, self._pl_responses = 0, []
        self.show_placement_item()

    def show_placement_item(self):
        self.clear_content()
        if self._pl_idx >= len(self.placement_live):
            self.finish_placement()
            return
        item = self.placement_live[self._pl_idx]
        self.dragon.set_mood("idle")
        c = Card(self.content)
        c.pack(fill="both", expand=True)

        Label(c, text=f"Placement · {self._pl_idx + 1} / {len(self.placement_live)}",
              bg=T.PANEL, fg=T.ACCENT, font=T.FS_SMALL).pack(anchor="w", padx=20, pady=(16, 2))
        prog = LangBar(c, width=400, height=6)
        prog.pack(anchor="w", padx=20)
        prog.set((self._pl_idx + 1) / len(self.placement_live))
        Label(c, text=item["prompt"], bg=T.PANEL, fg=T.TEXT,
              font=T.FS_TITLE).pack(anchor="w", padx=20, pady=18)

        if "options" in item:
            for i, opt in enumerate(item["options"]):
                b = PillButton(c, f"{i + 1}.  {opt}", width=340,
                               command=lambda i=i: self.answer_placement(i == item["answer"]))
                b.pack(pady=4)
                self._opt_buttons.append(b)
        else:
            self.entry = self._entry_row(c, placeholder="введи перевод…")
            self.entry.focus_force()
            self.entry.bind("<Key>", lambda e: self.on_input())
            self.entry.bind("<Return>", lambda e: self._submit_placement_type())
            PillButton(c, "Проверить  ⏎", command=self._submit_placement_type,
                       width=200, color=T.ACCENT, hover="#ffc866", fg="#241a05").pack(pady=8)
        self.toast.show("Адаптивный IRT: уровень уточняется с каждым ответом", T.TEXT_DIM, ms=2000)

    def current_expected(self):
        return self.placement_live[self._pl_idx]["expected"]

    def typed_text(self) -> str:
        v = self.entry.get()
        return "" if v in ("введи перевод…", "напиши перевод…") else v

    def _submit_placement_type(self):
        credit = core.partial_credit(self.current_expected(), self.typed_text())
        self.answer_placement(credit)

    def answer_placement(self, correct):
        if self._lock:
            return
        self._pl_responses.append(float(correct))
        self._pl_idx += 1
        self.show_placement_item()

    def finish_placement(self):
        res = core.placement_run(self.placement_live, self._pl_responses)
        self.st["placement_done"] = True
        self.st["level"] = res["assigned_level"]
        save_state(self.st)
        self.update_dashboard()
        self.mode = "home"
        self.clear_content()
        self.dragon.set_mood("cheer")
        c = Card(self.content)
        c.pack(fill="both", expand=True)
        Label(c, text=f"Твой стартовый уровень: {res['assigned_level']}",
              bg=T.PANEL, fg=T.GREEN, font=T.FS_HERO).pack(pady=(90, 8))
        Label(c, text=f"θ = {res['theta']}  ·  вопросов: {len(self._pl_responses)}",
              bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_BODY).pack()
        PillButton(c, "▶  К первому уроку", command=self.start_lesson, width=260,
                   height=50, color=T.GREEN, hover="#54e0a4", fg="#0d1b14").pack(pady=26)

    # ------------------------------------------------------ lesson
    def start_lesson(self):
        self.mode = "lesson"
        self.session_answers = []
        self.tasks = list(self.lesson_live["tasks"])
        self.task_i = 0
        self.show_task()

    def _task_header(self, c, t):
        lang_tag = t["lang"]
        Label(c, text=f"{self.lesson_live['title']}   ·   {self.task_i + 1} / {len(self.tasks)}",
              bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL).pack(anchor="w", padx=20, pady=(16, 2))
        prog = LangBar(c, lang=lang_tag, width=400, height=6)
        prog.pack(anchor="w", padx=20)
        prog.set((self.task_i + 1) / len(self.tasks))
        badge = Label(c, text=f"  {LANG_NAMES[lang_tag]}  ", bg=T.LANG_COLORS[lang_tag],
                      fg="#0d1420", font=T.FS_SMALL)
        badge.pack(anchor="w", padx=20, pady=(10, 0))

    def show_task(self):
        self.clear_content()
        if self.task_i >= len(self.tasks):
            self.finish_lesson()
            return
        t = self.tasks[self.task_i]
        self.current_task = t
        self.lang_of_current = t["lang"]
        self.task_events, self.task_start_wall = [], time.time()
        self.last_input_t = time.time()
        self._dimmed = False
        self._lock = False
        self.task_events.append((0, "focus_in"))
        self.dragon.set_mood("idle")

        c = Card(self.content)
        c.pack(fill="both", expand=True)
        self._task_header(c, t)
        Label(c, text=t["prompt"], bg=T.PANEL, fg=T.TEXT,
              font=T.FS_PROMPT).pack(anchor="w", padx=20, pady=16)

        if "options" in t:
            for i, opt in enumerate(t["options"]):
                b = PillButton(c, f"{i + 1}.  {opt}", width=340,
                               accent=T.LANG_COLORS[t["lang"]],
                               command=lambda i=i: self.submit_choice(i))
                b.pack(pady=4)
                self._opt_buttons.append(b)
        else:
            self.entry = self._entry_row(c, placeholder="напиши перевод…")
            self.entry.focus_force()
            self.entry.bind("<Key>", lambda e: self.on_input())
            self.entry.bind("<Return>", lambda e: self.submit_type())
            PillButton(c, "Проверить  ⏎", command=self.submit_type, width=200,
                       color=T.ACCENT, hover="#ffc866", fg="#241a05").pack(pady=8)

    def _hotkey(self, idx):
        if self._lock or self.mode not in ("lesson", "placement"):
            return
        if self.mode == "lesson" and idx < len(self._opt_buttons):
            self._opt_buttons[idx]._on_click(None)
        elif self.mode == "placement" and idx < len(self._opt_buttons):
            self._opt_buttons[idx]._on_click(None)

    def submit_choice(self, idx):
        if self._lock:
            return
        self._last_clicked_idx = idx
        self.on_input()
        t = self.current_task
        self.record_answer(1.0 if idx == t["answer"] else 0.0)

    def submit_type(self):
        if self._lock:
            return
        self.on_input()
        t = self.current_task
        self.record_answer(core.partial_credit(t["expected"], self.typed_text()))

    def save_now(self):
        self.st["pace"] = {"samples": self.pace.samples}
        save_state(self.st)

    def record_answer(self, correct: float):
        self._lock = True
        t = self.current_task   # задание нужно ниже для реестра слов UserData
        end_rel = int((time.time() - self.task_start_wall) * 1000)
        active = core.active_ms_from_events(self.task_events, end_rel)
        kind = self.current_task["kind"]
        verdict = core.judge_task(kind, active, self.pace)
        afk_suspect = verdict["afk_suspect"]
        weight = verdict["weight_ms"]
        if verdict["record_to_pace"]:
            self.pace.add(kind, active)

        cid = self.current_task.get("id", f"{self.lesson_live['id']}:{self.task_i}")
        card = core.CardState(**self.st["cards"].get(cid, {}))
        now_ms = int(time.time() * 1000)
        if afk_suspect:
            grade = 3 if correct >= 0.999 else 1
        else:
            grade = (4 if correct >= 1 and active < 5000 else
                     3 if correct >= 0.999 else 2 if correct >= 0.5 else 1)
        newcard = core.next_state(card, grade, now_ms)
        cd = dict(newcard.__dict__)
        # UserData-журнал сервера читает last_grade (верно/неверно) и реестр
        # слов words{} — см. local_server/server.py::_compute_userdata.
        cd["last_grade"] = grade
        self.st["cards"][cid] = cd
        self.st.setdefault("words", {})[cid] = {
            "text": t.get("_tgt_text") or t.get("prompt"),
            "lang": t.get("lang"),
            "source": t.get("source"),
            "license": t.get("license")}

        self.session_answers.append(core.AnswerRecord(
            kind=kind, correct=correct, active_ms=active,
            afk_excluded=afk_suspect, too_fast=(active < 700)))

        self.st["minutes"][self.lang_of_current] = \
            self.st["minutes"].get(self.lang_of_current, 0.0) + weight / 60_000
        self.save_now()
        self.update_dashboard()

        # ---- фидбек: подсветка + тост, затем следующее задание
        if afk_suspect:
            self.dragon.set_mood("angry")
            self.toast.show(self.tr("afk") + f" Вес задания: {weight/1000:.1f} с.", T.RED)
        elif correct >= 0.999:
            self.dragon.set_mood("cheer")
            due_d = (newcard.due_ms - now_ms) / 86_400_000
            self.toast.show(f"{self.tr('correct')} Повтор через {due_d:.1f} дн.", T.GREEN)
            self._highlight_right()
        elif correct > 0:
            self.dragon.set_mood("sad")
            self.toast.show(self.tr("partial") + f" ({correct:.0%})", T.ACCENT)
        else:
            self.dragon.set_mood("sad")
            self.toast.show(self.tr("wrong"), T.RED)
        if correct < 0.5 and not afk_suspect:
            self._mark_wrong_choice()

        self.task_i += 1
        self.tk.after(FEEDBACK_MS, self.show_task)

    def _highlight_right(self):
        t = self.current_task
        if "options" in t and 0 <= t["answer"] < len(self._opt_buttons):
            b = self._opt_buttons[t["answer"]]
            b._redraw(T.GREEN)
            b.set_text_color("#0d1b14")

    def _mark_wrong_choice(self):
        idx = getattr(self, "_last_clicked_idx", None)
        t = self.current_task
        if idx is not None and "options" in t and idx < len(self._opt_buttons):
            b = self._opt_buttons[idx]
            b._redraw(T.RED)
            b.set_text_color("#2a0d0d")
        self._highlight_right_if_needed(t)

    def _highlight_right_if_needed(self, t):
        if "options" in t and 0 <= t["answer"] < len(self._opt_buttons):
            b = self._opt_buttons[t["answer"]]
            b._redraw(T.GREEN)
            b.set_text_color("#0d1b14")

    # ------------------------------------------------------ results
    def finish_lesson(self):
        self.mode = "done"
        score = core.score_lesson(self.session_answers, self.pace)
        grade = core.lesson_score_to_grade(score)
        self.st["history"].append({"lesson": self.lesson_live["id"], "score": score,
                                   "ts": int(time.time()), "n": len(self.session_answers)})
        self.save_now()
        self.update_dashboard()
        self.clear_content()
        self.dragon.set_mood("cheer" if score >= 70 else "sad")

        c = Card(self.content)
        c.pack(fill="both", expand=True)
        head = Frame(c, bg=T.PANEL); head.pack(fill="x", padx=24, pady=(22, 4))
        Label(head, text="Урок завершён", bg=T.PANEL, fg=T.TEXT,
              font=T.FS_TITLE).pack(side="left")
        Label(head, text=["😞", "🙂", "😀", "🏆"][grade - 1], bg=T.PANEL,
              font=T.font(20)).pack(side="right")

        big = tk.Canvas(c, width=140, height=140, bg=T.PANEL, highlightthickness=0)
        big.pack(pady=10)
        frac = score / 100
        big.create_oval(15, 15, 125, 125, outline=T.BORDER, width=10)
        if frac > 0:
            big.create_arc(15, 15, 125, 125, start=90, extent=-360 * frac,
                           style="arc", outline=(T.GREEN if score >= 70 else
                                                 T.ACCENT if score >= 40 else T.RED), width=10)
        big.create_text(70, 70, text=f"{score}", fill=T.TEXT, font=T.font(28, "bold"))
        big.create_text(70, 100, text="/ 100", fill=T.TEXT_DIM, font=T.FS_SMALL)

        n_afk = sum(a.afk_excluded for a in self.session_answers)
        acc = sum(a.correct for a in self.session_answers) / max(1, len(self.session_answers))
        stats = Frame(c, bg=T.PANEL); stats.pack(pady=6)
        for lbl, val in [("Заданий", str(len(self.session_answers))),
                         ("Точность", f"{acc:.0%}"),
                         ("АФК-подозрений", str(n_afk)),
                         ("Грейд урока", str(grade))]:
            cell = Frame(stats, bg=T.PANEL); cell.pack(side="left", padx=14)
            Label(cell, text=val, bg=T.PANEL, fg=T.TEXT, font=T.FS_TITLE).pack()
            Label(cell, text=lbl, bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL).pack()

        mins = " · ".join(f"{LANG_NAMES[l]}: {m:.1f} мин" for l, m in self.st["minutes"].items())
        Label(c, text=mins, bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL).pack(pady=(4, 14))

        btns = Frame(c, bg=T.PANEL); btns.pack()
        again = PillButton(btns, "↻  Ещё раз", command=self.start_lesson, width=180,
                           color=T.GREEN, hover="#54e0a4", fg="#0d1b14")
        again.pack(side="left", padx=6)
        PillButton(btns, "Домой", command=self.show_home, width=140).pack(side="left", padx=6)
        self.space_job = self.tk.bind("<KeyRelease-space>", self._results_space)
        self.toast.show("Пробел — повторить урок", T.TEXT_DIM, ms=2000)

    def _results_space(self, _e=None):
        if self.mode != "done":
            self.tk.unbind("<KeyRelease-space>", self.space_job)
            return "break"
        self.tk.unbind("<KeyRelease-space>", self.space_job)
        self.start_lesson()
        return "break"

    # ------------------------------------------------------ настройки v3
    def show_settings(self):
        self.mode = "settings"
        self.clear_content()
        c = Card(self.content)
        c.pack(fill="both", expand=True)
        Label(c, text="⚙  Настройки", bg=T.PANEL, fg=T.TEXT,
              font=T.FS_TITLE).pack(anchor="w", padx=20, pady=(16, 8))

        s = self.st["settings"]

        # ---- тема (реальное переключение + рестарт UI)
        row1 = Frame(c, bg=T.PANEL); row1.pack(fill="x", padx=20, pady=4)
        Label(row1, text="Тема:", bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_BODY).pack(side="left")
        for name, lbl in (("dark", "🌙 Тёмная"), ("light", "☀ Светлая")):
            cur = s.get("theme", "dark") == name
            PillButton(row1, ("● " if cur else "○ ") + lbl, width=150, height=36,
                       color=T.GREEN if cur else T.PANEL_HI,
                       command=lambda n=name: self._set_theme(n)).pack(side="left", padx=4)

        # ---- язык приложения (скан lang_packs/app_ui)
        row2 = Frame(c, bg=T.PANEL); row2.pack(fill="x", padx=20, pady=4)
        Label(row2, text="Язык интерфейса:", bg=T.PANEL, fg=T.TEXT_DIM,
              font=T.FS_BODY).pack(side="left")
        ui_packs = packscan.scan_app_ui()
        cb = ttk.Combobox(row2, state="readonly", width=18,
                          values=[f"{p['name']} ({p['code']})" for p in ui_packs] or ["ru"])
        sel = next((i for i, p in enumerate(ui_packs)
                    if p["code"] == s.get("ui_lang", "ru")), 0)
        if ui_packs:
            cb.current(sel)
            cb.pack(side="left", padx=6)
            cb.bind("<<ComboboxSelected>>",
                    lambda e: self._set_ui_lang(ui_packs[cb.current()]["code"]))
        else:
            Label(row2, text="пакеты не найдены (lang_packs/app_ui)", bg=T.PANEL,
                  fg=T.RED, font=T.FS_SMALL).pack(side="left")
        PillButton(row2, "↻ Обновить список", width=150, height=32,
                   command=self.show_settings).pack(side="left", padx=6)

        # ---- пакеты изучения (study: «язык из языка»)
        row2b = Frame(c, bg=T.PANEL); row2b.pack(fill="x", padx=20, pady=4)
        Label(row2b, text="Пакеты для изучения:", bg=T.PANEL, fg=T.TEXT_DIM,
              font=T.FS_BODY).pack(side="left")
        study = packscan.scan_study()
        names = ", ".join(p.get("name", p["_path"]) for p in study) or "—"
        Label(row2b, text=f"{len(study)} шт · {names} (заглушка)", bg=T.PANEL,
              fg=T.TEXT, font=T.FS_SMALL).pack(side="left", padx=6)

        # ---- аккаунт / вход-регистрация
        auth = Card(c); auth.pack(fill="x", padx=20, pady=10)
        self._ensure_server()                     # автоподнятие локального сервера
        online = self.sync.health()
        who = f" · 👤 {self.sync.username}" if self.sync.token else ""
        port = self.sync.base.rsplit(":", 1)[-1]
        state_lbl = ("в сети ✓" if online else
                     "недоступен ✗ (запусти: python3 local_server/server.py)")
        Label(auth, text=(f"Сервер: {self.sync.base} — {state_lbl}" + who),
              bg=T.PANEL, fg=T.GREEN if online else T.RED, font=T.FS_SMALL).pack(anchor="w", padx=10, pady=(8, 2))
        fr = Frame(auth, bg=T.PANEL); fr.pack(fill="x", padx=6, pady=4)
        self._acc_user = self._entry_row(fr, placeholder="логин")
        self._acc_pass = self._entry_row(fr, placeholder="пароль")
        br = Frame(auth, bg=T.PANEL); br.pack(pady=6)
        PillButton(br, self.tr("register"), width=140, height=36, color=T.ACCENT,
                   hover="#ffc866", fg="#241a05", command=self._do_register).pack(side="left", padx=4)
        PillButton(br, self.tr("login"), width=140, height=36, color=T.BLUE,
                   hover="#7dbcff", command=self._do_login).pack(side="left", padx=4)
        if self.sync.token:
            PillButton(br, "Выход", width=110, height=36,
                       command=self._do_logout).pack(side="left", padx=4)

        # ---- сбросы
        row3 = Frame(c, bg=T.PANEL); row3.pack(fill="x", padx=20, pady=8)
        PillButton(row3, "Сбросить скорость (темп-медианы)", width=280, height=40,
                   color=T.RED, hover="#ff9494", fg="#2a0d0d",
                   command=self._reset_pace).pack(side="left", padx=4)
        PillButton(row3, "Сбросить весь прогресс", width=220, height=40,
                   color=T.RED, hover="#ff9494", fg="#2a0d0d",
                   command=self._reset_progress).pack(side="left", padx=4)

        # ---- синхронизация
        row4 = Frame(c, bg=T.PANEL); row4.pack(fill="x", padx=20, pady=(2, 10))
        PillButton(row4, "⇧ " + self.tr("sync_now") + " (push)", width=190, height=38,
                   color=T.GREEN, hover="#54e0a4", fg="#0d1b14",
                   command=self._do_push).pack(side="left", padx=4)
        PillButton(row4, "⇩ Затянуть (pull)", width=180, height=38,
                   command=self._do_pull).pack(side="left", padx=4)
        PillButton(row4, "← Домой", width=120, height=38,
                   command=self.show_home).pack(side="left", padx=4)

    # ---- обработчики настроек
    def _restart_ui(self):
        for w in self.sidebar.winfo_children():
            w.destroy()
        self.sidebar = self._build_sidebar(self.tk)
        self.sidebar.pack(side="left", fill="y", padx=(T.PAD, 0), pady=T.PAD)
        self.update_dashboard()

    def _set_theme(self, name: str):
        self.st["settings"]["theme"] = name
        self.apply_theme(name)
        self.save_now()
        self._restart_ui()
        self.show_settings()
        self.toast.show(f"Тема: {name}", T.BLUE)

    def _set_ui_lang(self, code: str):
        self.st["settings"]["ui_lang"] = code
        self.load_strings(code)
        self.save_now()
        self._restart_ui()
        self.show_settings()
        self.toast.show(f"Язык интерфейса: {code}")

    def _auth_fields(self):
        u, p = self._acc_user.get(), self._acc_pass.get()
        if u == "логин":
            u = ""
        if p == "пароль":
            p = ""
        return u.strip().lower(), p

    def _store_account(self):
        self.st["account"] = {"username": self.sync.username, "token": self.sync.token}
        self.save_now()

    def _ensure_server(self):
        """Автоподнятие локального сервера синхронизации (главная жалоба:
        «сервер падает / самозакрывается»).

        Причина «самозакрытия» при запуске двойным кликом по .py на Windows —
        консоль исчезает вместе с ошибкой. Поэтому приложение само запускает
        local_server/server.py отдельным процессом (DETACHED на Windows),
        ждёт /health и пишет лог в ~/.threelanguage/local_server.log — если
        что-то не так, пользователь видит причину в тосте, а не мелькание окна.
        """
        import subprocess, sys
        if self.sync.health():
            return
        server_py = Path(__file__).resolve().parent.parent / "local_server" / "server.py"
        if not server_py.exists():
            self.toast.show("local_server/server.py не найден — синхронизация офлайн", T.RED)
            return
        log_path = Path.home() / ".threelanguage" / "local_server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(log_path, "ab") as logf:
                kwargs = {}
                if sys.platform == "win32":       # скрыть консоль, но процесс живёт
                    kwargs = {"creationflags": 0x00000008 | 0x00000200}  # DETACHED | NO_WINDOW
                else:
                    kwargs = {"start_new_session": True}
                env = dict(os.environ, TL_PARENT_PID=str(os.getpid()), TL_GUI_APP="1")
                # Если GUI-сессии задан LAN-хост (TL_SERVER_HOST=0.0.0.0),
                # запоминаем его в ~/.threelanguage/server_host — клиент
                # SyncClient будет бить на этот адрес вместо 127.0.0.1.
                lan_host = (os.environ.get("TL_SERVER_HOST") or "").strip()
                if lan_host and lan_host not in ("127.0.0.1", "localhost"):
                    try:
                        (log_path.parent / "server_host").write_text(lan_host)
                    except OSError:
                        pass
                # явный pid родителя: сервер сам завершится, когда GUI закрыт
                subprocess.Popen([sys.executable, str(server_py)],
                                 stdout=logf, stderr=logf, cwd=str(server_py.parent),
                                 env=env, **kwargs)
                self._srv_spawned = True   # сервер подняли МЫ — при закрытии окна
                                           # on_close попросит его остановиться
        except Exception as e:
            self.toast.show(f"не удалось поднять сервер: {e}", T.RED)
            return
        # НЕ спать в UI-потоке (time.sleep(0.2)*25 вешал окно настроек на 5 с).
        # Неблокирующий опрос через tk.after; повторный вход в настройки не
        # поднимает второй сервер и не плодит циклы опроса.
        if getattr(self, "_srv_poll_job", None):
            self.tk.after_cancel(self._srv_poll_job)
        self._srv_poll_deadline = time.time() + 6.0
        self._srv_log_path = log_path
        self._poll_server()

    def _poll_server(self):
        self._srv_poll_job = None
        if self.sync.health():
            self.toast.show(f"локальный сервер поднят: {self.sync.base}", T.GREEN)
            return
        if time.time() >= self._srv_poll_deadline:
            tail = ""
            try:
                lines = self._srv_log_path.read_text(errors="replace").splitlines()
                tail = (" · " + lines[-1]) if lines else ""
            except OSError:
                pass
            self.toast.show("сервер не отвечает" + tail +
                            f" · полный лог: {self._srv_log_path}", T.RED)
            return
        self._srv_poll_job = self.tk.after(200, self._poll_server)

    def _heartbeat(self):
        """Пока окно открыто и пользователь залогинен — тихо стучимся на
        сервер (/health обновляет hb-файл): это подтверждает orphan-watchdog,
        что хозяин жив. Не залогинен — не дергаем сеть каждые 5 с; если сам
        GUI поднял сервер (_ensure_server), heartbeat идёт пока ждём /health."""
        self._hb_job = None
        try:
            if self.sync.token or getattr(self, "_srv_poll_deadline", 0) > time.time():
                self.sync.health()
        except Exception:
            pass
        self._hb_job = self.tk.after(5000, self._heartbeat)

    def on_close(self, event=None):
        """Закрытие окна: гасим heartbeat и останавливаем сервер, который
        подняли сами (POST /shutdown). Осиротевший python-процесс не должен
        переживать приложение — иначе он блокирует удаление папки проекта."""
        try:
            if getattr(self, "_hb_job", None):
                self.tk.after_cancel(self._hb_job)
                self._hb_job = None
        except Exception:
            pass
        try:
            if getattr(self, "_srv_spawned", False):
                # shutdown() сам переберёт порты-кандидаты: base клиента мог
                # остаться на устаревшем порте из port-file (сервер переехал)
                self.sync.shutdown()
        except Exception:
            pass
        return True

    def _do_register(self):
        ok, msg = self.sync.register(*self._auth_fields())
        if ok:
            self._store_account()
        self.toast.show("Регистрация: " + msg, T.GREEN if ok else T.RED)
        self.show_settings()

    def _do_login(self):
        ok, msg = self.sync.login(*self._auth_fields())
        if ok:
            self._store_account()
        self.toast.show(msg, T.GREEN if ok else T.RED)
        self.show_settings()

    def _do_logout(self):
        self.sync.logout()
        self.st["account"] = {"username": None, "token": None}
        self.save_now()
        self.show_settings()

    def _do_push(self):
        if not self.sync.token:
            self.toast.show("Сначала войди или зарегистрируйся", T.ACCENT)
            return
        self.save_now()
        ok, msg = self.sync.push_state(self.st)
        self.toast.show("Push: " + msg, T.GREEN if ok else T.RED)

    def _do_pull(self):
        if not self.sync.token:
            self.toast.show("Сначала войди или зарегистрируйся", T.ACCENT)
            return
        st, msg = self.sync.pull_state()
        if st:
            self.st = ensure_settings(st)
            self.pace = core.PaceStats(**self.st["pace"])
            roll_day(self.st)
            self.save_now()
            self._restart_ui()
            self.show_home()
            self.toast.show("Pull: состояние загружено с сервера", T.GREEN)
        else:
            self.toast.show("Pull: " + msg, T.RED)

    def _reset_pace(self):
        """Сброс темпа: pace_samples обнуляются (docs/AFK.md — медиана заново)."""
        self.pace.samples = {}
        self.st["pace"] = {"samples": {}}
        self.save_now()
        self.toast.show("Темп-статистика сброшена", T.BLUE)

    def _reset_progress(self):
        """Полный сброс: SRS-карточки, история, placement, минуты."""
        self.st = ensure_settings(default_state())
        self.pace = core.PaceStats(**self.st["pace"])
        save_state(self.st)
        self._restart_ui()
        self.show_home()
        self.toast.show("Прогресс сброшен", T.RED)


def main():
    # Понятный отказ вместо «молча крашнулся»: не хватает модулей — говорим что именно.
    missing = [m for m in ("theme", "widgets", "content", "sync_client", "packs")
               if not (Path(__file__).resolve().parent / f"{m}.py").exists()]
    if missing:
        sys.exit("Не найдены файлы приложения: " + ", ".join(f"build/{m}.py" for m in missing)
                 + "\nСкачай всю папку build/ целиком (plus shared/algorithm/reference.py).")
    root = TkinterDnD.Tk() if TkinterDnD else Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
