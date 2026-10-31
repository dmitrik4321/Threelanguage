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
# Клик по рамке/полю должен фокусить Entry. Под Xvfb focus_get работает только
# с видимым окном (у withdrawed-окна фокус всегда None), поэтому окно видно,
# но спрятано за пределы экрана (-geometry +5000+5000).
r3_geo = _r3.geometry(); _r3.deiconify(); _r3.geometry("+5000+5000"); _r3.update()
_box = e.master
_ph_before = e.get()  # плейсхолдер должен стоять до фокуса/ввода
e.focus_force(); _r3.update()
check("поле ввода: клик фокусит (focus_force-механизм)", _r3.focus_get() is e)
check("плейсхолдер показан до ввода", _ph_before == "напиши перевод…")
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

# --- v10: сиротский сервер не должен переживать GUI (баг: python оседал в
# диспетчере задач и блокировал удаление папки проекта). Тест поднимает
# сервер как «ребёнок» фиктивного родителя (--parent-pid), глушит hb-стук,
# убивает родителя и ждёт, что сервер завершится сам.
import subprocess as _sp
_server_py = BUILD.parent / "local_server" / "server.py"
if _server_py.exists():
    try:
        import flask as _flask  # noqa: F401
        _have_flask = True
    except ImportError:
        _have_flask = False
    if _have_flask:
        _fake_parent = _sp.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        # Тест полностью изолирован: свой HOME (свой lock/hb/port-файлы), свой
        # порт 8791 и TL_PARENT_PID=-1 — watchdog принудительно выключен.
        # Иначе тест убивал бы общий ~/.threelanguage/server.lock и зависал,
        # если основной dev-сервер уже запущен (тот держит lock).
        _test_home = Path(tempfile.mkdtemp(prefix="tl_smoke_"))
        _env = {**__import__("os").environ, "TL_SERVER_PORT": "8791",
                "HOME": str(_test_home), "USERPROFILE": str(_test_home),
                "TL_PARENT_PID": "-1"}
        _srv = _sp.Popen([sys.executable, str(_server_py),
                          "--parent-pid", str(_fake_parent.pid),
                          "--orphan-grace", "2"], env=_env,
                         stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
        _hb = _test_home / ".threelanguage" / "server_hb"   # изолированный HOME теста
        _up = False
        for _ in range(50):
            try:
                _hb.unlink(missing_ok=True)   # стук GUI не подтверждает хозяина —
                                              # проверяем именно реакцию на смерть
                                              # родителя, а не на таймаут hb
                with __import__("urllib.request", fromlist=["urlopen"]).urlopen(
                        "http://127.0.0.1:8791/health", timeout=1) as _r:
                    if b"threelanguage-local" in _r.read():
                        _up = True; break
            except Exception:
                pass
            time.sleep(0.2)
        check("v10: сервер-ребёнок поднялся", _up and _srv.poll() is None)
        _fake_parent.terminate(); _fake_parent.wait(timeout=5)
        try:
            _srv.wait(timeout=20)
            _exited = True
        except _sp.TimeoutExpired:
            _srv.kill(); _srv.wait(timeout=5); _exited = False
        check("v10: после смерти родителя сервер завершился сам (нет осиротевшего python)",
              _up and _exited and _srv.returncode == 0)
        (_Path := _test_home / ".threelanguage" / "server.lock")
        check("v10: lock-файл освобождён", not _Path.exists() or
              (_Path.read_text().strip() != str(_srv.pid)))
    else:
        print("SKIP v10 orphan-тест (нет flask)")
else:
    print("SKIP v10 orphan-тест (нет local_server/server.py)")

print("\nSMOKE", "OK" if not fails else f"FAILED: {fails}")
sys.exit(0 if not fails else 1)
