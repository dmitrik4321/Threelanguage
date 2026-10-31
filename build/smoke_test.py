"""Смоук-тест Python-билда без живого пользователя: гоняем App под Xvfb,
эмулируем клики/ввод с намеренными паузами и проверяем сквозные связки
placement → урок → АФК → скоринг → сохранение состояния.

Запуск:  xvfb-run -a python3 build/smoke_test.py
"""
import sys, time, tempfile
from pathlib import Path

BUILD = Path(__file__).resolve().parent
sys.path.insert(0, str(BUILD))

import app as A

# изолированная БД
tmp = tempfile.mkdtemp(prefix="tl_smoke_")
A.DB_PATH = Path(tmp) / "desktop.db"

try:
    from tkinter import Tk
except Exception as e:
    print("SKIP (нет tkinter/Tk):", e); sys.exit(2)

root = Tk()
root.withdraw()
app = A.App(root)

def pump(ms: int):
    """Прокрутить event loop заданное время."""
    end = time.time() + ms / 1000
    while time.time() < end:
        root.update()
        time.sleep(0.02)

fails = []
def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond: fails.append(name)

# --- placement: кликаем правильные ответы кнопок / вводим текст
for i in range(len(A.PLACEMENT_ITEMS)):
    pump(60)
    item = A.PLACEMENT_ITEMS[app._pl_idx]
    if "options" in item:
        # найти нужную кнопку
        for w in app.body.winfo_children():
            if hasattr(w, "cget") and w.cget("text") == item["options"][item["answer"]]:
                w.invoke(); break
    else:
        app.entry.insert(0, item["expected"][0])
        app._submit_placement_type()
pump(1500)
check("placement завершён и уровень присвоен", app.st["placement_done"] and app.st["level"] in range(1, 7))
check("после placement стартовал урок", app.mode in ("lesson", "done"))

# --- урок: отвечаем; на 4-м задании имитируем АФК (пауза > порога медианы)
tasks = list(A.LESSON["tasks"])
for idx, t in enumerate(tasks):
    pump(80)
    if app.mode != "lesson":
        break
    if idx == 3:
        # «отошёл»: спим, потом печатаем — idle вычтем через focus_out/focus_in
        time.sleep(0.05)
        app.task_events.append((int((time.time() - app.task_start_wall) * 1000), "focus_out"))
        time.sleep(0.05)
        app.on_input()
    if "options" in t:
        app.submit_choice(t["answer"])
    else:
        app.entry.insert(0, t["expected"][0]); app.submit_type()
    pump(900)  # ждём after(700) show_task

pump(1200)
check("урок доигран до экрана итога", app.mode == "done")
check("оценка урока в диапазоне 0..100", 0 <= app.st["history"][-1]["score"] <= 100)
check("карточки SRS записаны", len(app.st["cards"]) >= 5)
check("pace_stats наполнены", sum(len(v) for v in app.pace.samples.values()) >= 5)
mins = sum(app.st["minutes"].values())
check("дневные минуты посчитаны (effective_ms)", mins > 0)

# --- перезапуск: состояние переживает рестарт
root.destroy()
app2_state = A.load_state()
check("состояние сохранено в SQLite", app2_state["placement_done"] and len(app2_state["cards"]) >= 5)

# --- юнит-проверка АФК-судейства ядром (×10 медианы, порог = max(10×med, 120с))
pace = A.core.PaceStats(samples={"CHOICE_R2T": [1200, 1500, 1100, 1400, 1300, 1250]})
v = A.core.judge_task("CHOICE_R2T", 130_000, pace)   # 130с > 120с floor → suspect
check("АФК: 130с при floor 120с → suspect, вес=медиана 1275мс", v["afk_suspect"] and v["weight_ms"] == 1275 and not v["record_to_pace"])
v90 = A.core.judge_task("CHOICE_R2T", 90_000, pace)  # docs/AFK.md: floor 120с защищает быстрые типы
check("АФК: 90с < MICRO_FLOOR 120с → не брак (по спеке)", not v90["afk_suspect"])
v_fast = A.core.judge_task("CHOICE_T2T", 600_000, A.core.PaceStats())  # нет замеров → ceiling 10 мин
check("АФК: новичок без замеров — порог CEILING 10мин", not v_fast["afk_suspect"])
v2 = A.core.judge_task("CHOICE_R2T", 1400, pace)
check("АФК: честный ответ не бракуется", not v2["afk_suspect"] and v2["record_to_pace"])

print("\nSMOKE", "OK" if not fails else f"FAILED: {fails}")
sys.exit(0 if not fails else 1)
