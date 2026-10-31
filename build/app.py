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
from tkinter import Entry, Frame, Label, Tk

ROOT = Path(__file__).resolve().parent.parent          # корень репозитория
sys.path.insert(0, str(ROOT / "shared" / "algorithm"))  # эталон ядра
sys.path.insert(0, str(Path(__file__).resolve().parent))

import reference as core                                 # noqa: E402
import theme as T                                        # noqa: E402
from content import LESSON, PLACEMENT_ITEMS              # noqa: E402
from widgets import Card, Dragon, LangBar, PillButton, Toast  # noqa: E402

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
    }


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

        self.st = load_state()
        roll_day(self.st)
        self.pace = core.PaceStats(**self.st["pace"])

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
        # KeyPress надёжнее KeyRelease+keysym Digit1 (Digit1 отсутствует в старых X keysym)
        for d in "1234":
            tk_root.bind(f"<KeyPress-{d}>", lambda e, i=int(d) - 1: self._hotkey(i))

        self.update_dashboard()
        self.show_home()
        self.schedule_idle_check()
        self.schedule_blink()

    # ------------------------------------------------------ sidebar
    def _build_sidebar(self, root):
        sb = Card(root)
        sb.configure(width=250)
        sb.pack_propagate(False)
        inner = Frame(sb, bg=T.PANEL)
        inner.pack(fill="both", expand=True, padx=T.CARD_PAD, pady=T.CARD_PAD)

        Label(inner, text="🐉 Threelanguage", bg=T.PANEL, fg=T.ACCENT,
              font=T.font(15, "bold")).pack(anchor="w")
        Label(inner, text=T.TXT["welcome"], bg=T.PANEL, fg=T.TEXT_DIM,
              font=T.FS_SMALL).pack(anchor="w", pady=(0, 10))

        self.dragon = Dragon(inner, size=120)
        self.dragon.pack(pady=(0, 12))

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
            PillButton(inner, "▶  Начать урок", command=self.start_lesson,
                       width=280, height=52, color=T.GREEN, hover="#54e0a4",
                       fg="#0d1b14").pack(pady=5)
            PillButton(inner, "Пройти placement заново", command=self.start_placement,
                       width=280).pack(pady=5)
        else:
            PillButton(inner, "▶  Placement-тест (~2 минуты)", command=self.start_placement,
                       width=280, height=52, color=T.ACCENT, hover="#ffc866",
                       fg="#241a05").pack(pady=5)
        hist = self.st["history"][-3:]
        if hist:
            txt = "Последние: " + " · ".join(f"{h['score']}/100" for h in hist)
            Label(inner, text=txt, bg=T.PANEL, fg=T.TEXT_DIM, font=T.FS_SMALL).pack(pady=(18, 0))
        Label(inner, text="Esc — домой · 1..4 — выбор варианта · Enter — проверить",
              bg=T.PANEL, fg=T.BORDER, font=T.FS_SMALL).pack(side="bottom", pady=(0, 8))

    # ------------------------------------------------------ idle / AFK watchdog
    def schedule_idle_check(self):
        self._idle_job = self.tk.after(2000, self._idle_tick)

    def _idle_tick(self):
        if self.mode == "lesson" and not self._dimmed:
            idle = (time.time() - self.last_input_t) * 1000
            if idle > IDLE_DIM_MS and self.task_events:
                self._dimmed = True
                self.task_events.append((int(idle), "focus_out"))
                self.dragon.set_mood("sleep")
                self.toast.show(T.TXT["sleep"], T.BLUE, ms=4000)
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

    # ------------------------------------------------------ placement
    def start_placement(self):
        self.mode = "placement"
        self._pl_idx, self._pl_responses = 0, []
        self.show_placement_item()

    def show_placement_item(self):
        self.clear_content()
        if self._pl_idx >= len(PLACEMENT_ITEMS):
            self.finish_placement()
            return
        item = PLACEMENT_ITEMS[self._pl_idx]
        self.dragon.set_mood("idle")
        c = Card(self.content)
        c.pack(fill="both", expand=True)

        Label(c, text=f"Placement · {self._pl_idx + 1} / {len(PLACEMENT_ITEMS)}",
              bg=T.PANEL, fg=T.ACCENT, font=T.FS_SMALL).pack(anchor="w", padx=20, pady=(16, 2))
        prog = LangBar(c, width=400, height=6)
        prog.pack(anchor="w", padx=20)
        prog.set((self._pl_idx + 1) / len(PLACEMENT_ITEMS))
        Label(c, text=item["prompt"], bg=T.PANEL, fg=T.TEXT,
              font=T.FS_TITLE).pack(anchor="w", padx=20, pady=18)

        if "options" in item:
            for i, opt in enumerate(item["options"]):
                b = PillButton(c, f"{i + 1}.  {opt}", width=340,
                               command=lambda i=i: self.answer_placement(i == item["answer"]))
                b.pack(pady=4)
                self._opt_buttons.append(b)
        else:
            self.entry = Entry(c, font=T.FS_TITLE, bg=T.BG, fg=T.TEXT,
                               insertbackground=T.TEXT, relief="flat", bd=0)
            ef = Card(c); ef.pack(padx=40, pady=6, fill="x")
            self.entry.pack(fill="x", padx=10, pady=8, in_=ef)
            self.entry.focus_set()
            self.entry.bind("<Key>", lambda e: self.on_input())
            self.entry.bind("<Return>", lambda e: self._submit_placement_type())
            PillButton(c, "Проверить  ⏎", command=self._submit_placement_type,
                       width=200, color=T.ACCENT, hover="#ffc866", fg="#241a05").pack(pady=8)
        self.toast.show("Адаптивный IRT: уровень уточняется с каждым ответом", T.TEXT_DIM, ms=2000)

    def current_expected(self):
        return PLACEMENT_ITEMS[self._pl_idx]["expected"]

    def _submit_placement_type(self):
        credit = core.partial_credit(self.current_expected(), self.entry.get())
        self.answer_placement(credit)

    def answer_placement(self, correct):
        if self._lock:
            return
        self._pl_responses.append(float(correct))
        self._pl_idx += 1
        self.show_placement_item()

    def finish_placement(self):
        res = core.placement_run(PLACEMENT_ITEMS, self._pl_responses)
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
        self.tasks = list(LESSON["tasks"])
        self.task_i = 0
        self.show_task()

    def _task_header(self, c, t):
        lang_tag = t["lang"]
        Label(c, text=f"{LESSON['title']}   ·   {self.task_i + 1} / {len(self.tasks)}",
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
            self.entry = Entry(c, font=T.FS_TITLE, bg=T.BG, fg=T.TEXT,
                               insertbackground=T.TEXT, relief="flat", bd=0)
            ef = Card(c); ef.pack(padx=40, pady=6, fill="x")
            self.entry.pack(fill="x", padx=10, pady=8, in_=ef)
            self.entry.focus_set()
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
        self.record_answer(core.partial_credit(t["expected"], self.entry.get()))

    def save_now(self):
        self.st["pace"] = {"samples": self.pace.samples}
        save_state(self.st)

    def record_answer(self, correct: float):
        self._lock = True
        end_rel = int((time.time() - self.task_start_wall) * 1000)
        active = core.active_ms_from_events(self.task_events, end_rel)
        kind = self.current_task["kind"]
        verdict = core.judge_task(kind, active, self.pace)
        afk_suspect = verdict["afk_suspect"]
        weight = verdict["weight_ms"]
        if verdict["record_to_pace"]:
            self.pace.add(kind, active)

        cid = self.current_task.get("id", f"{LESSON['id']}:{self.task_i}")
        card = core.CardState(**self.st["cards"].get(cid, {}))
        now_ms = int(time.time() * 1000)
        if afk_suspect:
            grade = 3 if correct >= 0.999 else 1
        else:
            grade = (4 if correct >= 1 and active < 5000 else
                     3 if correct >= 0.999 else 2 if correct >= 0.5 else 1)
        newcard = core.next_state(card, grade, now_ms)
        self.st["cards"][cid] = newcard.__dict__

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
            self.toast.show(T.TXT["afk"] + f" Вес задания: {weight/1000:.1f} с.", T.RED)
        elif correct >= 0.999:
            self.dragon.set_mood("cheer")
            due_d = (newcard.due_ms - now_ms) / 86_400_000
            self.toast.show(f"{T.TXT['correct']} Повтор через {due_d:.1f} дн.", T.GREEN)
            self._highlight_right()
        elif correct > 0:
            self.dragon.set_mood("sad")
            self.toast.show(T.TXT["partial"] + f" ({correct:.0%})", T.ACCENT)
        else:
            self.dragon.set_mood("sad")
            self.toast.show(T.TXT["wrong"], T.RED)
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
        self.st["history"].append({"lesson": LESSON["id"], "score": score,
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


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
