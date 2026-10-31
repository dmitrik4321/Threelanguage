"""Threelanguage — Python-билд десктоп-приложения (tkinter).

Запуск:  python3 build/app.py     (без сборки EXE, нужен только Python 3.10+)

Что внутри (по фиксированным решениям HANDOFF):
  * Ядро НЕ дублируется — импортируется из shared/algorithm/reference.py
    (SRS, AFK-темп-монитор ×10 медианы, оценка урока 0..100, placement IRT,
    partial_credit NFKC+Levenshtein для CJK).
  * Заглушка курса «Как переводится Hi? → Привет/Пока/Корова» (build/content.py).
  * Прогресс: минуты на каждый язык отдельно + дневной прогресс-бар (3 языка).
  * АФК: idle > 25с вычитается из активного времени; ≥10×медианы темпа →
    вес = медиана, раздутый замер не попадает в среднее; сонный дракон при idle > 90с.
  * Состояние (карточки SRS, pace_stats, дневные минуты) — SQLite в ~/.threelanguage/.

Это тестовый билд: UI один экран, аудио/говорение — заглушки.
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path
import tkinter as tk
from tkinter import Canvas, Frame, Label, Tk, Entry, ttk

ROOT = Path(__file__).resolve().parent.parent          # /workspace
sys.path.insert(0, str(ROOT / "shared" / "algorithm"))  # эталон ядра
sys.path.insert(0, str(ROOT / "build"))

import reference as core                                # noqa: E402
from content import LESSON, PLACEMENT_ITEMS             # noqa: E402

DB_PATH = Path.home() / ".threelanguage" / "desktop.db"
MASCOT_DIR = ROOT / "assets" / "mascot"
LANG_NAMES = {"en": "English", "pt-BR": "Português", "zh": "中文"}
DAILY_GO_MIN = 3           # минут на язык за день (прогресс-бар)
IDLE_DIM_MS = 90_000       # docs/AFK.md: idle > 90с → затемнение с dragon_sleep

try:
    from PIL import Image, ImageTk
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False


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
    con.execute("INSERT OR REPLACE INTO kv VALUES ('state', ?)", (json.dumps(st, ensure_ascii=False),))
    con.commit()
    con.close()


def default_state() -> dict:
    day = time.strftime("%Y-%m-%d")
    return {
        "day": day,
        "cards": {},                                  # task_id -> CardState dict
        "pace": {"samples": {}},                      # PaceStats
        "minutes": {l: 0.0 for l in LANG_NAMES},      # effective_session_ms по языкам
        "history": [],                                # [{lesson, score, ms}]
        "placement_done": False,
        "level": None,
    }


def roll_day(st: dict):
    today = time.strftime("%Y-%m-%d")
    if st.get("day") != today:
        st["day"] = today
        st["minutes"] = {l: 0.0 for l in LANG_NAMES}


# ---------------------------------------------------------------- mascots
class Mascot:
    """PNG маскота через Pillow; без Pillow — серый круг-заглушка."""
    def __init__(self, canvas: Canvas):
        self.canvas = canvas
        self.photos = {}
        self.mood = "idle"

    def set_mood(self, mood: str):
        self.mood = mood
        self.draw()

    def draw(self):
        c = self.canvas
        c.delete("all")
        w, h = int(c["width"]), int(c["height"])
        name = {"idle": "dragon_idle", "cheer": "dragon_cheer", "sad": "dragon_sad",
                "angry": "dragon_angry", "sleep": "dragon_sleep"}.get(self.mood, "dragon_idle")
        if _HAS_PIL:
            p = MASCOT_DIR / f"{name}.png"
            if p.exists():
                if name not in self.photos:
                    img = Image.open(p).convert("RGBA").resize((w - 8, h - 8), Image.NEAREST)
                    self.photos[name] = ImageTk.PhotoImage(img)
                c.create_image(w // 2, h // 2, image=self.photos[name])
                return
        c.create_oval(w * .15, h * .15, w * .85, h * .85, fill="#b3402e", outline="#7a2a1c")


# ---------------------------------------------------------------- app
class App:
    def __init__(self, tk_root: Tk):
        self.tk = tk_root
        tk_root.title("Threelanguage — Python test build (не EXE)")
        tk_root.geometry("860x640")
        tk_root.configure(bg="#f5efe6")

        self.st = load_state()
        roll_day(self.st)
        self.pace = core.PaceStats(**self.st["pace"])

        # верхняя панель: дракон + дневные прогресс-бары
        top = Frame(tk_root, bg="#f5efe6")
        top.pack(fill="x", padx=12, pady=8)
        self.mascot_canvas = Canvas(top, width=140, height=140, bg="#f5efe6", highlightthickness=0)
        self.mascot_canvas.grid(row=0, column=0, rowspan=3)
        self.mascot = Mascot(self.mascot_canvas)
        self.bars = {}
        for i, lang in enumerate(LANG_NAMES):
            Label(top, text=f"{lang}  ({DAILY_GO_MIN} мин/день)", bg="#f5efe6",
                  font=("TkDefaultFont", 10, "bold")).grid(row=i, column=1, sticky="w", padx=10)
            pb = ttk.Progressbar(top, length=260, maximum=DAILY_GO_MIN)
            pb.grid(row=i, column=2, padx=6, pady=3)
            lbl = Label(top, text="", bg="#f5efe6", width=10)
            lbl.grid(row=i, column=3, sticky="w")
            self.bars[lang] = (pb, lbl)
        self.status = Label(tk_root, text="", bg="#f5efe6", fg="#5a4632", anchor="w")
        self.status.pack(fill="x", padx=16)

        # средняя область: задание
        self.body = Frame(tk_root, bg="#fffaf0", bd=1, relief="solid")
        self.body.pack(fill="both", expand=True, padx=12, pady=8)

        # нижняя панель: кнопки
        self.controls = Frame(tk_root, bg="#f5efe6")
        self.controls.pack(fill="x", padx=12, pady=(0, 10))

        self.mode = None            # 'placement' | 'lesson' | 'done'
        self.task_events: list[tuple[int, str]] = []
        self.task_start_wall = 0.0
        self.last_input_t = 0.0
        self.current_task = None
        self.session_answers: list[core.AnswerRecord] = []
        self.lang_of_current = "en"
        self._pl_idx = 0
        self._pl_responses: list[float] = []
        self._idle_job = None
        self._dimmed = False

        self.update_bars()
        if self.st["placement_done"]:
            self.start_lesson()
        else:
            self.start_placement()
        self.schedule_idle_check()

    # ------------------------------------------------------ helpers UI
    def clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    def set_status(self, s: str):
        self.status.config(text=s)

    def update_bars(self):
        roll_day(self.st)
        for lang, (pb, lbl) in self.bars.items():
            m = self.st["minutes"].get(lang, 0.0)
            pb["value"] = min(m, DAILY_GO_MIN)
            lbl.config(text=f"{m:.1f} мин")

    # ------------------------------------------------------ idle / AFK watchdog
    def schedule_idle_check(self):
        self._idle_job = self.tk.after(2000, self._idle_tick)

    def _idle_tick(self):
        # глобальный простой (нет ввода) — для «сонного дракона» и честного active_ms
        if self.mode == "lesson" and not self._dimmed:
            idle = (time.time() - self.last_input_t) * 1000
            if idle > IDLE_DIM_MS and self.task_events:
                self._dimmed = True
                self.task_events.append((int(idle), "focus_out"))  # балда: не считаем
                self.mascot.set_mood("sleep")
                self.set_status("Дракон ждёт… (простой не учитывается)")
        self._idle_job = self.tk.after(2000, self._idle_tick)

    def on_input(self):
        now_rel = int((time.time() - self.task_start_wall) * 1000)
        if self._dimmed:
            self._dimmed = False
            self.task_events.append((now_rel, "focus_in"))
            self.mascot.set_mood("idle")
        self.task_events.append((now_rel, "input"))
        self.last_input_t = time.time()

    # ------------------------------------------------------ placement
    def start_placement(self):
        self.mode = "placement"
        self._pl_idx, self._pl_responses = 0, []
        self.show_placement_item()

    def show_placement_item(self):
        self.clear_body()
        if self._pl_idx >= len(PLACEMENT_ITEMS):
            self.finish_placement()
            return
        item = PLACEMENT_ITEMS[self._pl_idx]
        Label(self.body, text=f"Placement-тест · вопрос {self._pl_idx + 1}/{len(PLACEMENT_ITEMS)}",
              bg="#fffaf0", font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=14, pady=(12, 2))
        Label(self.body, text=item["prompt"], bg="#fffaf0", font=("TkDefaultFont", 14)).pack(anchor="w", padx=14, pady=6)
        if "options" in item:
            for i, opt in enumerate(item["options"]):
                b = ttk.Button(self.body, text=opt, command=lambda i=i: self.answer_placement(i == item["answer"]))
                b.pack(fill="x", padx=60, pady=3)
        else:
            self.entry = Entry(self.body, font=("TkDefaultFont", 14))
            self.entry.pack(fill="x", padx=60, pady=6)
            self.entry.bind("<Key>", lambda e: self.on_input())
            ttk.Button(self.body, text="Ответить",
                       command=self._submit_placement_type).pack(pady=4)
        self.set_status("Определяем твой уровень (адаптивный IRT, старт θ=3.5)")

    def _submit_placement_type(self):
        txt = self.entry.get()
        credit = core.partial_credit(self.current_expected(), txt)
        self.answer_placement(credit)

    def current_expected(self):
        return PLACEMENT_ITEMS[self._pl_idx]["expected"]

    def answer_placement(self, correct):
        y = float(correct)
        self._pl_responses.append(y)
        self._pl_idx += 1
        self.show_placement_item()

    def finish_placement(self):
        res = core.placement_run(PLACEMENT_ITEMS, self._pl_responses)
        self.st["placement_done"] = True
        self.st["level"] = res["assigned_level"]
        save_state(self.st)
        self.set_status(f"Уровень определён: {res['assigned_level']} (θ={res['theta']}) — стартуем урок")
        self.mascot.set_mood("cheer")
        self.tk.after(900, self.start_lesson)

    # ------------------------------------------------------ lesson
    def start_lesson(self):
        self.mode = "lesson"
        self.session_answers = []
        self.tasks = list(LESSON["tasks"])
        self.task_i = 0
        self.show_task()

    def show_task(self):
        self.clear_body()
        if self.task_i >= len(self.tasks):
            self.finish_lesson()
            return
        t = self.tasks[self.task_i]
        self.current_task = t
        self.lang_of_current = t["lang"]
        self.task_events, self.task_start_wall = [], time.time()
        self.last_input_t = time.time()
        self._dimmed = False
        self.task_events.append((0, "focus_in"))
        self.mascot.set_mood("idle")

        Label(self.body, text=f"Урок «{LESSON['title']}» · {self.task_i + 1}/{len(self.tasks)} · {t['kind']} · {t['lang']}",
              bg="#fffaf0", font=("TkDefaultFont", 10, "bold"), fg="#8a6d4b").pack(anchor="w", padx=14, pady=(12, 2))
        Label(self.body, text=t["prompt"], bg="#fffaf0", font=("TkDefaultFont", 16)).pack(anchor="w", padx=14, pady=8)

        if "options" in t:
            for i, opt in enumerate(t["options"]):
                ttk.Button(self.body, text=opt,
                           command=lambda i=i: self.submit_choice(i)).pack(fill="x", padx=80, pady=3)
        else:
            self.entry = Entry(self.body, font=("TkDefaultFont", 15))
            self.entry.pack(fill="x", padx=80, pady=6)
            self.entry.focus_set()
            self.entry.bind("<Key>", lambda e: self.on_input())
            self.entry.bind("<Return>", lambda e: self.submit_type())
            ttk.Button(self.body, text="Проверить (Enter)", command=self.submit_type).pack(pady=4)
        self.set_status("Отвечай. Паузы >25с и автокликер отсекает темп-монитор.")

    def submit_choice(self, idx):
        self.on_input()
        t = self.current_task
        correct = 1.0 if idx == t["answer"] else 0.0
        self.record_answer(correct)

    def submit_type(self):
        self.on_input()
        t = self.current_task
        credit = core.partial_credit(t["expected"], self.entry.get())
        self.record_answer(credit)

    def record_answer(self, correct: float):
        end_rel = int((time.time() - self.task_start_wall) * 1000)
        active = core.active_ms_from_events(self.task_events, end_rel)
        kind = self.current_task["kind"]
        verdict = core.judge_task(kind, active, self.pace)
        afk_suspect = verdict["afk_suspect"]
        weight = verdict["weight_ms"]
        if verdict["record_to_pace"]:
            self.pace.add(kind, active)

        # SRS: карточка по id задания (в тест-билде колода = список заданий урока)
        cid = self.current_task.get("id", f"{LESSON['id']}:{self.task_i}")
        card = core.CardState(**self.st["cards"].get(cid, {}))
        now_ms = int(time.time() * 1000)
        if afk_suspect:
            grade = 3 if correct >= 0.999 else 1   # попытка засчитана по весу-медиане
        else:
            grade = 4 if correct >= 1 and active < 5000 else (3 if correct >= 0.999 else 2 if correct >= 0.5 else 1)
        newcard = core.next_state(card, grade, now_ms)
        self.st["cards"][cid] = newcard.__dict__

        self.session_answers.append(core.AnswerRecord(
            kind=kind, correct=correct, active_ms=active,
            afk_excluded=afk_suspect, too_fast=(active < 700)))

        # минуты идут по weight_ms (effective_session_ms), не wall-clock
        self.st["minutes"][self.lang_of_current] = \
            self.st["minutes"].get(self.lang_of_current, 0.0) + weight / 60_000
        save_state(self.st)
        self.update_bars()

        if afk_suspect:
            self.mascot.set_mood("angry")
            self.set_status(f"АФК? Замедление ≥10×медианы — вес задания = {weight/1000:.1f}с, раздутый замер в статистику не попал.")
        elif correct >= 0.999:
            self.mascot.set_mood("cheer")
            self.set_status(f"Верно! Карточка → {newcard.state}, интервал след. повтора ≈ {(newcard.due_ms - now_ms) / 86_400_000:.1f} дн.")
        elif correct > 0:
            self.mascot.set_mood("sad")
            self.set_status(f"Частично верно ({correct:.0%}) — опечатка прощена (NFKC+Levenshtein).")
        else:
            self.mascot.set_mood("sad")
            self.set_status("Неверно. Карточка вернётся быстрее (Again/Hard).")

        self.task_i += 1
        self.tk.after(700, self.show_task)

    # ------------------------------------------------------ finish
    def finish_lesson(self):
        self.mode = "done"
        score = core.score_lesson(self.session_answers, self.pace)
        grade = core.lesson_score_to_grade(score)
        self.st["history"].append({"lesson": LESSON["id"], "score": score,
                                   "ts": int(time.time()), "n": len(self.session_answers)})
        save_state(self.st)
        self.clear_body()
        emoji = ["😞", "🙂", "😀", "🏆"][grade - 1]
        Label(self.body, text=f"Урок завершён {emoji}", bg="#fffaf0",
              font=("TkDefaultFont", 26, "bold")).pack(pady=(30, 6))
        Label(self.body, text=f"Оценка: {score}/100 (грейд {grade})", bg="#fffaf0",
              font=("TkDefaultFont", 18)).pack()
        n_afk = sum(a.afk_excluded for a in self.session_answers)
        Label(self.body, text=f"Заданий: {len(self.session_answers)}, АФК-подозрений: {n_afk}. "
                              f"Минуты дня: " + ", ".join(f"{l}={m:.1f}" for l, m in self.st['minutes'].items()),
              bg="#fffaf0", fg="#5a4632").pack(pady=8)
        ttk.Button(self.body, text="Ещё раз тот же урок", command=self.start_lesson).pack(pady=4)
        ttk.Button(self.body, text="Пройти placement заново",
                   command=lambda: (self.st.update(placement_done=False), save_state(self.st), self.start_placement())).pack(pady=4)
        self.mascot.set_mood("cheer" if score >= 70 else "sad")
        self.set_status("Тест-билд: полная база уроков и синхронизация с backend — впереди.")


def main():
    root = Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
