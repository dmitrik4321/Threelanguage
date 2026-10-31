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

root = Tk()
root.withdraw()
app = A.App(root)
check("старт на домашнем экране", app.mode == "home")
if app.st.get("placement_done"):
    app.st.update(placement_done=False, level=None); A.save_state(app.st)
    app.show_home()
pump(80)
app.start_placement()  # та же команда, что у кнопки «Placement»

# --- placement: кликаем правильные ответы кнопок / вводим текст
for i in range(len(A.PLACEMENT_ITEMS)):
    pump(60)
    item = A.PLACEMENT_ITEMS[app._pl_idx]
    if "options" in item:
        app._opt_buttons[item["answer"]]._on_click(None)
    else:
        app.entry.insert(0, item["expected"][0])
        app._submit_placement_type()
pump(1500)
check("placement завершён и уровень присвоен", app.st["placement_done"] and app.st["level"] in range(1, 7))
check("после placement — экран уровня, урок не стартовал сам", app.mode == "home")
pump(80)
app.start_lesson()
check("кнопка/команда запуска урока работает", app.mode == "lesson")

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
        app._opt_buttons[t["answer"]]._on_click(None)   # реальный путь кнопки
    else:
        app.entry.insert(0, t["expected"][0]); app.submit_type()
    pump(1000)  # ждём after(FEEDBACK_MS) show_task

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

# --- сонный дракон: idle-триггер watchdoga (UI-слой, ядро не трогает)
class _FakeDragon:
    def set_mood(self, m): self.mood = m
    def tick(self): pass

class _FakeToast:
    def __init__(self): self.shown = []
    def show(self, text, color=None, ms=2600): self.shown.append(text)

app.toast = _FakeToast()   # предыдущий root уничтожен — старые tk-виджеты мертвы
app.mode = "lesson"; app.task_events = [(0, "focus_in")]; app._dimmed = False
app.last_input_t = time.time() - 100  # 100с без ввода
app.dragon = _FakeDragon()
app._idle_tick()
check("watchdog: idle>90с → дракон спит, событие focus_out записано",
      app.dragon.mood == "sleep" and any(e[1] == "focus_out" for e in app.task_events))
app.on_input()
check("возврат: focus_in + дракон проснулся",
      app.dragon.mood == "idle" and any(e[1] == "focus_in" for e in app.task_events))

# --- v3: PNG-ассет дракона (красный, не процедурный зелёный)
from widgets import Dragon as _D, MOOD_TO_PNG as _M
_r2 = Tk(); _r2.withdraw()
_dg = _D(_r2, size=96); _r2.update()
check("дракон: использует готовые PNG-ассеты (idle)", ("dragon_idle", 96) in _dg._photos)
_dg.set_mood("cheer"); _r2.update()
check("дракон: смена настроения грузит нужный PNG", ("dragon_cheer", 96) in _dg._photos)
import theme as _T
_T.set_theme("light"); _bg_before = _T.BG
_T.set_theme("dark")
check("тема: light/dark переключаются через set_theme", _bg_before == "#f2f4f8" and _T.BG == "#141a26")

# --- v3: кликабельное поле ввода (_entry_row + плейсхолдер)
_r3 = Tk(); _r3.withdraw()
app_for_entry = None
class _MiniApp:
    tk = _r3
mini = _MiniApp()
e = A.App._entry_row(mini, _r3, placeholder="напиши перевод…")
_r3.update()
check("поле ввода: внутри рамки-boxа (не в_ чужом родителе)", e.winfo_parent().count(".") >= 1)
e.event_generate("<Button-1>")   # клик по самому полю
_r3.update()
check("поле ввода: клик фокусит", _r3.focus_get() is e)
check("плейсхолдер показан до ввода", e.get() == "напиши перевод…")
_r3.destroy()

# --- v3: скан языковых пакетов
import packs as _P
ui = _P.scan_app_ui(); study = _P.scan_study()
check("пакеты app_ui: ru и en находятся сканером", {p["code"] for p in ui} >= {"ru", "en"})
check("пакеты study: формат язык-из-языка (ru-en, ru-zh)",
      any(p.get("source") == "ru" and p.get("target") == "en" for p in study))
s_en = _P.strings_for("en")
check("строки en подтягиваются из пакета", s_en.get("correct", "").startswith("Correct"))

# --- v3: sync_client против живого локального сервера (если запущен)
from sync_client import SyncClient
sc = SyncClient()
if sc.health():
    ok, msg = sc.register(f"smoke{int(time.time())%100000}", "testpass")
    if not ok:
        ok, msg = sc.login(f"smoke{int(time.time())%100000}", "testpass")
    check("sync: регистрация/вход на localhost", ok)
    ok, msg = sc.push_state({"hello": 1, "history": []})
    check("sync: push состояния", ok)
    st, msg = sc.pull_state()
    check("sync: pull возвращает отправленное", st == {"hello": 1, "history": []})
else:
    print("SKIP sync (локальный сервер не запущен)")

print("\nSMOKE", "OK" if not fails else f"FAILED: {fails}")
sys.exit(0 if not fails else 1)
