"""Threelanguage — локальный сервер синхронизации + админ-панель (Flask).

Запуск:  python3 local_server/server.py   →  http://127.0.0.1:8765

Роль в архитектуре (docs/ARCHITECTURE.md): временная замена backend/ для
локального теста синхронизации десктоп-билда. Протокол совпадает с эндпоинтами
FastAPI-скелета (auth/register, auth/login, sync/push, sync/pull, stats), но
хранение — SQLite в ~/.threelanguage/local_server.db, пароли — pbkdf2_hmac.

Админ-панель (/admin): статистика аккаунтов и уроков, просмотр сохранённых
состояний клиентов («вставить свой пример» — форма JSON поверх реальной схемы
состояния из build/app.py default_state()).
"""
from __future__ import annotations

"""Локальный сервер Threelanguage: auth, sync, админ-панель (Flask + SQLite, 127.0.0.1).

Lock-файл от двойного запуска; живость pid проверяется БЕЗ os.kill (на Windows это
TerminateProcess!) — OpenProcess/GetExitCodeProcess + опрос /health.
Watchdog (TL_PARENT_PID) завершает сервер после смерти GUI-родителя.
/admin — статистика пользователей и вставка своих примеров.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
from pathlib import Path

try:
    from flask import (Flask, jsonify, request, redirect, render_template_string,
                       send_from_directory, g)
except ImportError as e:  # pragma: no cover
    raise SystemExit("Нужен Flask: pip install flask") from e

DB_PATH = Path.home() / ".threelanguage" / "local_server.db"
PORT_FILE = Path.home() / ".threelanguage" / "server_port"
LOCK_FILE = Path.home() / ".threelanguage" / "server.lock"
HB_PATH = Path.home() / ".threelanguage" / "server_hb"
PORT = int(os.environ.get("TL_SERVER_PORT", "8765"))
# --------------------------------------------------------- админ-доступ ----
# Учётные данные админки лежат в ФАЙЛЕ КОНФИГА В КОРНЕ репозитория:
#   admin_credentials.json  ->  {"username": "admin", "password": "..."}
# Сервер читает его при старте; если файла нет — создаёт сам с паролем по
# умолчанию и предупреждает в консоли. TL_ADMIN_TOKEN задаётся через env и
# полностью ПЕРЕКРЫВАЕТ файл (для тех, кому не хочется хранить пароль на диске).
# Осознанно простая схема «файл + запрос пароля»: сервер слушает только
# 127.0.0.1, угрозная модель локальная. Пароль сравнивается hmac.compare_digest.
ADMIN_CREDS_PATH = Path(__file__).resolve().parent.parent / "admin_credentials.json"
ADMIN_DEFAULT_USER = "admin"
ADMIN_DEFAULT_PASS = "threelanguage-admin"


def _load_admin_creds() -> tuple[str, str]:
    """Читает admin_credentials.json; при отсутствии/битости — дефолт + запись файла."""
    try:
        cfg = json.loads(ADMIN_CREDS_PATH.read_text(encoding="utf-8"))
        user = str(cfg.get("username") or ADMIN_DEFAULT_USER)
        pw = str(cfg.get("password") or "")
        if pw:
            return user, pw
    except (OSError, ValueError):
        pass
    cfg = {"username": ADMIN_DEFAULT_USER, "password": ADMIN_DEFAULT_PASS,
           "_comment": "Локальный доступ к /admin. Меняй password под себя; "
                       "сервер слушает только 127.0.0.1."}
    try:
        ADMIN_CREDS_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
        print(f"[admin] Создан {ADMIN_CREDS_PATH.name}: "
              f"{ADMIN_DEFAULT_USER}/{ADMIN_DEFAULT_PASS} — смени пароль!")
    except OSError:
        pass
    return ADMIN_DEFAULT_USER, ADMIN_DEFAULT_PASS


ADMIN_USER, ADMIN_PASSWORD = _load_admin_creds()
ADMIN_TOKEN = os.environ.get("TL_ADMIN_TOKEN", "")  # env-оверрайд поверх файла
ADMIN_SESSIONS: dict[str, float] = {}               # cookie-token -> expiry
ADMIN_COOKIE = "tl_admin"
ADMIN_TTL = 8 * 3600                               # сессия админки — 8 часов


def _admin_logged_in() -> bool:
    tok = request.cookies.get(ADMIN_COOKIE, "")
    exp = ADMIN_SESSIONS.get(tok)
    return bool(tok and exp and exp > time.time())


def _pid_alive(pid: int) -> bool:
    """Жив ли процесс — БЕЗ os.kill на Windows.

    Критический баг прежней проверки: на Windows os.kill(pid, N) при любом
    N, кроме CTRL_C_EVENT/CTRL_BREAK_EVENT, вызывает TerminateProcess —
    т.е. «проба живости» УБИВАЛА процесс. Если pid из stale-lock после
    перезагрузки рециклировался и принадлежал GUI-приложению, открытие
    настроек (Popen сервера → acquire_lock → os.kill) мгновенно закрывало
    окно пользователя. На Unix os.kill(pid, 0) остаётся безвредным probe.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong(0)
            ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
            return bool(ok) and code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:      # чужой процесс — он жив
        return True
    except OSError as e:
        # EINVAL и прочее: не уверены — считаем живым, чтобы никогда никого
        # не «похоронить» ложным verdict. Watchdog сам разберётся по hb.
        import errno
        return e.errno != errno.ESRCH


def _health_ok(port: int) -> bool:
    """Наш ли сервер уже слушает: /health с app == threelanguage-local."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.5) as r:
            return (_json.loads(r.read().decode("utf-8", "replace"))
                    .get("app") == "threelanguage-local")
    except Exception:
        return False


def _lock_pid(path: Path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return -1


def acquire_lock():
    """Один экземпляр сервера. Решение — по ФАКТУ слушания порта, а не по
    тому, жив ли pid из lock-файла (pid ничего не гарантирует: он мог
    рециклироваться в чужой процесс). Механизм:
      • наш /health отвечает            → «уже запущен», вежливый выход;
      • не отвечает, pid мёртв          → stale-lock, чистим и стартуем;
      • не отвечает, pid жив (чужой)    → ждём 1–2 с (сервер мог только
        стартовать), переспрашиваем /health, потом всё равно чистим.
    Никакого os.kill для чужих pid — никого не убиваем."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    port_now = PORT
    if PORT_FILE.exists():
        try:
            port_now = int(PORT_FILE.read_text().strip())
        except ValueError:
            pass
    if LOCK_FILE.exists():
        pid = _lock_pid(LOCK_FILE)
        if _health_ok(port_now):
            print(f"[!] Сервер уже запущен: http://127.0.0.1:{port_now} "
                  f"(pid lock: {pid}). Выход, ничего не убивая.")
            raise SystemExit(0)
        if pid != os.getpid() and _pid_alive(pid):
            for _ in range(8):                    # до ~2 секунд
                time.sleep(0.25)
                if _health_ok(port_now):
                    print(f"[!] Сервер поднялся параллельно: http://127.0.0.1:{port_now}")
                    raise SystemExit(0)
            print(f"[!] В lock pid {pid}, но он не наш сервер (/health молчит) — "
                  f"stale-lock, перезабираем.")
        LOCK_FILE.unlink(missing_ok=True)         # мусор от упавшего — чистим
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)


app = Flask(__name__, static_folder=None)   # статику раздаём своими маршрутами ниже

# ------------------------------------------------------------- веб-клиент ----
# local_server/webapp/ — HTML+CSS+JS перенос десктопного билда (build/app.py).
# Ядро там — shared/algorithm/web_core.js (порт reference.py, паритет в тестах).
WEBAPP_DIR = Path(os.environ.get("TL_WEBAPP_DIR", str(Path(__file__).resolve().parent / "webapp")))
CORE_JS = Path(__file__).resolve().parent.parent / "shared" / "algorithm" / "web_core.js"
MASCOT_DIR = Path(__file__).resolve().parent.parent / "assets" / "mascot"
REPO_ROOT = Path(__file__).resolve().parent.parent

CLIENT_KEY = "tl_client"          # g по запросу: web | desktop | admin


def _client_kind() -> str:
    """Кто стучится: явный заголовок X-Client (веб-клиент шлёт 'web' всегда),
    иначе admin (cookie-сессия админки / X-Admin-Token), иначе 'desktop' для
    запросов с Bearer-токеном (так ходит sync_client.py), иначе 'web'.

    Веб-трафик дополнительно уточняется по User-Agent и cookie tl_served,
    которое ставит / при отдаче страницы: мобильный браузер логируется как
    'web-mobile', десктопный — 'web'. API-запросы от мобилки несут UA телефона
    в каждом fetch, поэтому классификация работает без доп. заголовков."""
    # 'web' от клиента — это базовое вебо; его ещё уточним по UA/cookie ниже.
    h = (request.headers.get("X-Client") or "").strip().lower()
    kind = h if h in ("desktop", "admin", "web", "web-mobile") else None
    if kind is None:
        if request.path.startswith("/admin") or \
                request.cookies.get(ADMIN_COOKIE) or \
                (ADMIN_TOKEN and request.headers.get("X-Admin-Token") == ADMIN_TOKEN):
            kind = "admin"
        elif request.headers.get("Authorization", "").startswith("Bearer "):
            kind = "desktop"
        else:
            kind = "web"
    # Уточнение для веба: сервер сам знает, какую страницу он отдал этому
    # браузеру (cookie tl_served); если cookie нет — свежая эвристика по UA.
    if kind == "web":
        # Явный override из app.js (мобильная сборка шлёт X-Client: web-mobile)
        # и cookie tl_served от / надёжнее эвристики по UA.
        if h == "web-mobile" or request.cookies.get("tl_served") == "web-mobile":  # см. выше
            kind = "web-mobile"
        elif not request.cookies.get("tl_served") and _is_mobile_ua(request.headers.get("User-Agent", "")):
            kind = "web-mobile"
    return kind


@app.before_request
def _tag_client():
    setattr(g, CLIENT_KEY, _client_kind())


@app.after_request
def _log_client(resp):
    """Каждый запрос пишется в stdout и в таблицу events с меткой клиента —
    веб-трафик отличается от десктопного во всех логах ([client=web])."""
    kind = getattr(g, CLIENT_KEY, _client_kind())
    line = (f"[client={kind}] {request.method} {request.path} -> {resp.status_code}"
            f" @ {time.strftime('%H:%M:%S')}")
    print(line, flush=True)
    try:
        with db() as con:
            con.execute("INSERT INTO events(ts, client, method, path, status) VALUES(?,?,?,?,?)",
                        (int(time.time()), kind, request.method, request.path[:120],
                         resp.status_code))
    except sqlite3.Error:
        pass       # лог не должен ронять запрос (например, при старте до init_db)
    return resp


# ----------------------- маршрутизация по User-Agent (десктоп v1 / мобилка v2) --
_UA_MOBILE_RE = re.compile(
    r"android|iphone|ipod|iemobile|blackberry|windows phone|webos|kindle|"
    r"opera mini|mobile safari|palm", re.I)


def _is_mobile_ua(ua: str) -> bool:
    """True, если User-Agent выглядит как телефон/планшет.

    Приоритет: явный override ?client=mobile|desktop и cookie tl_client_choice
    (переключалки в обеих страницах — можно принудительно посмотреть чужую v).
    Иначе эвристика по UA: мобильные токены; отдельно iPadOS >= 13, который
    притворяется macOS-десктопом, но тачевик (Safari на планшете => мобильная v).
    """
    ua = ua or ""
    if "android" in ua.lower() and "Mobile Safari" in ua and "Version/" not in ua:
        return False          # WebView внутри Android-приложения — не браузер-телефон
    low = ua.lower()
    if _UA_MOBILE_RE.search(low):
        return True
    if "macintosh" in low and "cpu os" in low and "safari" in low \
            and "touchscreen" in request.headers.get("X-Architecture", "").lower():
        return True           # iPadOS desktop-UA обманка (ограничение: см. README PR)
    return False


@app.get("/")
def web_index():
    """Кто стучится по User-Agent — тому своя версия клиента.

    Мобильный UA получает mobile.html (v2), остальным — index.html (v1).
    Выбор пишется в лог запроса ([client=web-mobile] / [client=web])."""
    choice = (request.args.get("client") or request.cookies.get("tl_client_choice") or "").lower()
    if choice == "mobile":
        serve_mobile, page = True, "mobile.html"
    elif choice == "desktop":
        serve_mobile, page = False, "index.html"
    else:
        serve_mobile = _is_mobile_ua(request.headers.get("User-Agent", ""))
        page = "mobile.html" if serve_mobile else "index.html"
    resp = send_from_directory(WEBAPP_DIR, page)
    resp.set_cookie("tl_served", "web-mobile" if serve_mobile else "web", max_age=3600, samesite="Lax")
    print(f"[ua-route] {'MOBILE' if serve_mobile else 'DESKTOP'} <- {request.headers.get('User-Agent', '')[:90]}", flush=True)
    return resp


@app.get("/desktop")
def web_desktop():
    """v1 всегда, независимо от User-Agent."""
    resp = send_from_directory(WEBAPP_DIR, "index.html")
    resp.set_cookie("tl_served", "web", max_age=3600, samesite="Lax")
    return resp


@app.get("/mobile")
def web_mobile():
    """v2 (мобильная адаптация) всегда, независимо от User-Agent."""
    resp = send_from_directory(WEBAPP_DIR, "mobile.html")
    resp.set_cookie("tl_served", "web-mobile", max_age=3600, samesite="Lax")
    return resp


@app.get("/static/<path:fname>")
def web_static(fname):
    return send_from_directory(WEBAPP_DIR / "static", fname)


@app.get("/core/web_core.js")
def web_core_js():
    return send_from_directory(CORE_JS.parent, CORE_JS.name, mimetype="application/javascript")


@app.get("/mascot/<path:fname>")
def web_mascot(fname):
    return send_from_directory(MASCOT_DIR, fname)


@app.get("/api/content/lesson")
def content_lesson():
    """Живой урок из того же модуля, что использует десктоп (build/data_sources.py)."""
    langs = (request.args.get("langs") or "en,pt-BR,zh").split(",")
    sys.path.insert(0, str(REPO_ROOT / "build"))
    try:
        import data_sources as ds
        lesson = ds.build_lesson([l.strip() for l in langs])
    except Exception as e:
        return jsonify({"error": f"content: {e}"}), 502
    return jsonify(lesson)


@app.get("/api/content/placement")
def content_placement():
    lang = request.args.get("lang") or "pt-BR"
    sys.path.insert(0, str(REPO_ROOT / "build"))
    try:
        import data_sources as ds
        items = ds.placement_items([lang])
    except Exception as e:
        return jsonify({"error": f"content: {e}"}), 502
    return jsonify({"items": items})


@app.get("/api/packs")
def api_packs():
    """Список языковых пакетов (тот же сканер lang_packs/, что у десктопа)."""
    sys.path.insert(0, str(REPO_ROOT / "build"))
    try:
        import packs
        app_ui = [p.get("code") or p.get("id", "ru") for p in packs.scan_app_ui()]
        learn = [{"id": p.get("id"), "src": p.get("source") or p.get("src"),
                  "tgt": p.get("target") or p.get("tgt"), "name": p.get("name")}
                 for p in packs.scan_study()]
    except Exception:
        app_ui, learn = ["ru"], []
    return jsonify({"app_ui": app_ui or ["ru"], "learn": learn})


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            pass_salt TEXT NOT NULL,
            pass_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS states(
            user_id INTEGER PRIMARY KEY,
            state_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            client TEXT NOT NULL,          -- web | web-mobile | desktop | admin
            method TEXT NOT NULL,
            path TEXT NOT NULL,
            status INTEGER NOT NULL);
        """)


def hash_pw(pw: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), 120_000).hex()


def make_token() -> str:
    return secrets.token_hex(24)


def current_user() -> sqlite3.Row | None:
    tok = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    if not tok:
        return None
    with db() as con:
        row = con.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
            (tok,)).fetchone()
    return row


def require_admin():
    """Пускать в админку держателя TL_ADMIN_TOKEN (заголовок X-Admin-Token —
    для скриптов) либо браузер с cookie-сессией после формы входа по паролю
    из admin_credentials.json. Возвращает None (допуск) или "login" (показать вход)."""
    if ADMIN_TOKEN and request.headers.get("X-Admin-Token") == ADMIN_TOKEN:
        return None
    if not ADMIN_TOKEN and _admin_logged_in():
        return None
    return "login"


# ------------------------------------------------------------------ API
@app.post("/api/auth/register")
def register():
    d = request.get_json(force=True, silent=True) or {}
    username = str(d.get("username", "")).strip().lower()
    password = str(d.get("password", ""))
    if len(username) < 3 or len(password) < 4:
        return jsonify({"error": "username>=3, password>=4"}), 400
    salt = secrets.token_hex(16)
    try:
        with db() as con:
            cur = con.execute(
                "INSERT INTO users(username, pass_salt, pass_hash, created_at) VALUES(?,?,?,?)",
                (username, salt, hash_pw(password, salt), int(time.time())))
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return jsonify({"error": "занято"}), 409
    tok = make_token()
    with db() as con:
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, uid, int(time.time())))
    return jsonify({"token": tok, "user_id": uid, "username": username})


@app.post("/api/auth/login")
def login():
    d = request.get_json(force=True, silent=True) or {}
    username = str(d.get("username", "")).strip().lower()
    password = str(d.get("password", ""))
    with db() as con:
        u = con.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not u or not hmac.compare_digest(u["pass_hash"], hash_pw(password, u["pass_salt"])):
        return jsonify({"error": "неверный логин/пароль"}), 401
    tok = make_token()
    with db() as con:
        con.execute("INSERT INTO sessions VALUES(?,?,?)", (tok, u["id"], int(time.time())))
    return jsonify({"token": tok, "user_id": u["id"], "username": username})


@app.post("/api/sync/push")
def push():
    u = current_user()
    if u is None:
        return jsonify({"error": "no auth"}), 401
    body = request.get_json(force=True, silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "bad json"}), 400
    with db() as con:
        con.execute("INSERT OR REPLACE INTO states VALUES(?,?,?)",
                    (u["id"], json.dumps(body, ensure_ascii=False), int(time.time())))
    return jsonify({"ok": True, "bytes": len(json.dumps(body))})


@app.get("/api/sync/pull")
def pull():
    u = current_user()
    if u is None:
        return jsonify({"error": "no auth"}), 401
    with db() as con:
        row = con.execute("SELECT state_json, updated_at FROM states WHERE user_id=?",
                          (u["id"],)).fetchone()
    if row is None:
        return jsonify({"state": None})
    return jsonify({"state": json.loads(row["state_json"]), "updated_at": row["updated_at"]})


@app.get("/api/stats")
def stats():
    u = current_user()
    if u is None:
        return jsonify({"error": "no auth"}), 401
    with db() as con:
        row = con.execute("SELECT state_json FROM states WHERE user_id=?", (u["id"],)).fetchone()
    st = json.loads(row["state_json"]) if row else {}
    hist = st.get("history", [])
    return jsonify({
        "lessons_done": len(hist),
        "best_score": max((h.get("score", 0) for h in hist), default=None),
        "minutes": st.get("minutes", {}),
        "level": st.get("level"),
        "cards": len(st.get("cards", {})),
    })


DEFAULT_EXAMPLE = {
    "day": time.strftime("%Y-%m-%d"),
    "cards": {"l1:t1": {"due_ms": int(time.time() * 1000) + 86_400_000,
                        "interval_d": 1.0, "ease": 2.5, "reps": 1, "lapses": 0,
                        "stability": 1.0, "difficulty": 5.0}},
    "pace": {"samples": {"translate": [1800, 2100, 2600]}},
    "minutes": {"en": 2.4, "pt-BR": 1.1, "zh": 0.8},
    "history": [{"lesson": "l1", "score": 87, "ts": int(time.time()), "n": 4}],
    "placement_done": True,
    "level": 3,
}


# ------------------------------------------------------------------ admin
ADMIN_TMPL = """<!doctype html><html><head><meta charset="utf-8">
<title>Threelanguage · админ-панель</title>
<style>
 body{background:#141a26;color:#e8edf5;font-family:monospace;margin:24px}
 a{color:#ffb638} h1{font-size:20px} h2{font-size:15px;color:#93a1b5;margin-top:26px}
 table{border-collapse:collapse;width:100%} td,th{border:1px solid #33415a;padding:6px 9px;
 font-size:12px;text-align:left} th{background:#1d2534}
 textarea{width:100%;height:260px;background:#1d2534;color:#e8edf5;border:1px solid #33415a;
 font-family:monospace;font-size:12px;padding:8px}
 button{background:#ffb638;color:#241a05;border:0;padding:8px 16px;font-weight:bold;cursor:pointer}
 .msg{color:#3ecf8e} .err{color:#ff6b6b}
</style></head><body>
<h1>&#128009; Threelanguage — локальная админ-панель</h1>
<form method=post action=/admin/logout style=float:right><button style="background:#33415a;color:#e8edf5;border:0;padding:4px 10px;cursor:pointer">Выйти</button></form>
<a href="/api/stats">API stats</a> · <a href="/health">health</a>

<h2>Пользователи</h2>
<table><tr><th>id</th><th>username</th><th>создан</th><th>урок.</th><th>лучший балл</th></tr>
{% for r in users %}<tr><td>{{r.id}}</td><td>{{r.username}}</td><td>{{r.created}}</td>
<td>{{r.lessons}}</td><td>{{r.best}}</td></tr>{% endfor %}</table>

<h2>Сохранённые состояния клиентов</h2>
{% for s in states %}
<div><b>#{{s.user_id}} · {{s.username}}</b> · обновлено {{s.updated}} · {{s.size}} байт</div>
<pre style="background:#1d2534;padding:8px;font-size:11px;overflow:auto">{{s.pretty}}</pre>
{% endfor %}{% if not states %}<i>пусто — пушни что-нибудь из приложения</i>{% endif %}

<h2>Активность клиентов (web vs desktop)</h2>
<table><tr><th>клиент</th><th>запросов всего</th><th>за последний час</th></tr>
{% for e in clients %}<tr><td>{{e.client}}</td><td>{{e.n}}</td><td>{{e.recent}}</td></tr>{% endfor %}</table>
<h2>Последние запросы</h2>
<table><tr><th>время</th><th>клиент</th><th>метод</th><th>путь</th><th>код</th></tr>
{% for e in events %}<tr><td>{{e.ts}}</td><td>{{e.client}}</td><td>{{e.method}}</td>
<td>{{e.path}}</td><td>{{e.status}}</td></tr>{% endfor %}</table>

<h2>Вставить свой пример (demo-state)</h2>
<p style="font-size:12px;color:#93a1b5">JSON в формате состояния клиента
(default_state из build/app.py). Сохранится под пользователем demo / demo1234.</p>
<textarea id="pj">{{example}}</textarea><br>
<button onclick="saveExample()">Сохранить как пример</button>
<span id="out"></span>

<script>
async function saveExample(){
  const txt=document.getElementById('pj').value;
  let st; try{st=JSON.parse(txt);}catch(e){document.getElementById('out').innerHTML=
    '<span class=err>Невалидный JSON: '+e+'</span>';return;}
  let r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:'demo',password:'demo1234'})});
  let j=await r.json();
  if(!j.token){ r=await fetch('/api/auth/register',{method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({username:'demo',password:'demo1234'})}); j=await r.json(); }
  r=await fetch('/api/sync/push',{method:'POST',
    headers:{'Content-Type':'application/json','Authorization':'Bearer '+j.token},
    body:JSON.stringify(st)});
  document.getElementById('out').innerHTML='<span class=msg>'+JSON.stringify(await r.json())+
    '</span>'; setTimeout(()=>location.reload(),700);
}
</script></body></html>"""


@app.get("/admin")
def admin():
    err = require_admin()
    if err:   # нет валидной сессии/токена — страница входа по паролю из конфига
        return login_page(), 401
    with db() as con:
        users = []
        for u in con.execute("SELECT * FROM users ORDER BY id"):
            srow = con.execute("SELECT state_json FROM states WHERE user_id=?",
                               (u["id"],)).fetchone()
            st = json.loads(srow["state_json"]) if srow else {}
            hist = st.get("history", [])
            users.append(dict(id=u["id"], username=u["username"],
                              created=time.strftime("%Y-%m-%d %H:%M",
                                                    time.localtime(u["created_at"])),
                              lessons=len(hist),
                              best=max((h.get("score", 0) for h in hist), default="-")))
        states = []
        for s in con.execute("""SELECT s.user_id, u.username, s.updated_at, s.state_json
                                FROM states s JOIN users u ON u.id=s.user_id
                                ORDER BY s.updated_at DESC"""):
            pretty = json.dumps(json.loads(s["state_json"]), ensure_ascii=False, indent=2)
            states.append(dict(user_id=s["user_id"], username=s["username"], size=len(s["state_json"]),
                               updated=time.strftime("%Y-%m-%d %H:%M:%S",
                                                     time.localtime(s["updated_at"])),
                               pretty=pretty[:4000]))
    example = json.dumps(DEFAULT_EXAMPLE, ensure_ascii=False, indent=2)
    # активность клиентов из таблицы events (пишет _log_client в after_request)
    clients, events = [], []
    try:
        since = int(time.time()) - 3600
        with db() as con:
            for r in con.execute("""SELECT client, COUNT(*) n,
                                    SUM(CASE WHEN ts>? THEN 1 ELSE 0 END) recent
                                    FROM events GROUP BY client ORDER BY n DESC""", (since,)):
                clients.append(dict(client=r["client"], n=r["n"], recent=r["recent"] or 0))
            for r in con.execute("""SELECT ts, client, method, path, status FROM events
                                    ORDER BY id DESC LIMIT 25"""):
                events.append(dict(ts=time.strftime("%H:%M:%S", time.localtime(r["ts"])),
                                   client=r["client"], method=r["method"],
                                   path=r["path"], status=r["status"]))
    except sqlite3.Error:
        pass
    return render_template_string(ADMIN_TMPL, users=users, states=states, example=example,
                                  clients=clients, events=events)


# ------------------------------------------------------ страница входа ----
LOGIN_TMPL = """<!doctype html><html><head><meta charset="utf-8">
<title>Threelanguage · вход в админку</title>
<style>
 body{background:#141a26;color:#e8edf5;font-family:monospace;display:flex;
      justify-content:center;align-items:center;min-height:90vh;margin:0}
 .card{background:#1d2534;border:1px solid #33415a;padding:28px 34px;width:320px}
 h1{font-size:17px;margin:0 0 6px} p{font-size:12px;color:#93a1b5;margin:0 0 18px}
 label{display:block;font-size:11px;color:#93a1b5;margin:10px 0 4px}
 input{width:100%;box-sizing:border-box;background:#141a26;color:#e8edf5;
       border:1px solid #33415a;padding:8px;font-family:monospace;font-size:13px}
 button{margin-top:18px;width:100%;background:#ffb638;color:#241a05;border:0;
        padding:9px;font-weight:bold;cursor:pointer;font-family:monospace}
 .err{color:#ff6b6b;font-size:12px;margin-top:12px}
</style></head><body>
<form class=card method=post action=/admin/login>
 <h1>&#128274; Threelanguage — админка</h1>
 <p>Локальный сервер. Учётные данные — в admin_credentials.json (корень проекта).</p>
 {% if bad %}<div class=err>Неверный логин или пароль.</div>{% endif %}
 <label>Пользователь</label><input name=username autocomplete=username>
 <label>Пароль</label><input name=password type=password autocomplete=current-password>
 <button>Войти</button>
</form></body></html>"""


def login_page(bad: bool = False):
    return render_template_string(LOGIN_TMPL, bad=bad)


@app.post("/admin/login")
def admin_login():
    """Проверка формы входа против конфига; успех — cookie-сессия + редирект."""
    form = request.form
    ok_user = hmac.compare_digest(str(form.get("username", "")), ADMIN_USER)
    ok_pass = hmac.compare_digest(str(form.get("password", "")), ADMIN_PASSWORD)
    if not (ok_user and ok_pass):
        return login_page(bad=True), 401
    tok = secrets.token_hex(16)
    ADMIN_SESSIONS[tok] = time.time() + ADMIN_TTL
    resp = redirect("/admin")
    resp.set_cookie(ADMIN_COOKIE, tok, max_age=ADMIN_TTL, httponly=True, samesite="Lax")
    print(f"[client=admin] login ok user={ADMIN_USER}", flush=True)
    return resp


@app.post("/admin/logout")
def admin_logout():
    ADMIN_SESSIONS.pop(request.cookies.get(ADMIN_COOKIE, ""), None)
    resp = redirect("/admin")
    resp.delete_cookie(ADMIN_COOKIE)
    return resp


@app.get("/health")
def health():
    try:   # «сердцебиение» для orphan-watchdog: каждый запрос GUI обновляет mtime
        HB_PATH.touch()
    except OSError:
        pass
    return jsonify({"ok": True, "ts": int(time.time()), "app": "threelanguage-local"})


@app.post("/shutdown")
def shutdown():
    """Вежливая остановка по запросу GUI (закрытие окна): сервер, который
    приложение подняло само, не должен осиротеть и держать папку проекта."""
    LOCK_FILE.unlink(missing_ok=True)
    threading.Timer(0.3, lambda: os._exit(0)).start()  # дать ответить на запрос
    return jsonify({"ok": True, "bye": True})


init_db()


def _free_port(preferred: int) -> int:
    """Первый свободный TCP-порт на 127.0.0.1, начиная с preferred."""
    import socket
    for p in [preferred] + [preferred + i for i in range(1, 20)]:
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit(f"Не нашёл свободный порт рядом с {preferred}")


if __name__ == "__main__":
    # Порт: TL_SERVER_PORT или 8765; если занят чужим процессом — автовыбор
    # следующего свободного + запись фактического порта в ~/.threelanguage/server_port,
    # чтобы десктоп-клиент сам нашёл сервер (см. build/sync_client.py).
    acquire_lock()
    chosen = _free_port(PORT)
    if chosen != PORT:
        print(f"[!] Порт {PORT} занят — использую {chosen}")
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(chosen))
    print(f"Threelanguage local server: http://127.0.0.1:{chosen}  "
          f"(админка: http://127.0.0.1:{chosen}/admin)")

    def _watch_parent():
        """Ребёнок не должен переживать родителя-приложение (осиротевший
        python блокировал удаление папки проекта). Родитель либо задан в
        аргументах (--parent-pid), либо это PARENT_PID из окружения GUI."""
        ppid = None
        for i, a in enumerate(sys.argv):
            if a == "--parent-pid" and i + 1 < len(sys.argv):
                ppid = int(sys.argv[i + 1])
        if not ppid:
            ppid = int(os.environ.get("TL_PARENT_PID") or 0)
        if not ppid or ppid <= 1 or ppid == os.getpid():
            return
        # Защита от рециклинга pid (особенно Windows): одной «живости» мало.
        # Хозяин подтверждён, если (а) его environ содержит TL_GUI_APP=1
        # (ставит app.py при Popen; на Linux читается, на Windows — нет) или
        # (б) он недавно ходил к нам по HTTP (sync каждые несколько секунд).
        # Мёртвый хозяин + тишина > grace -> закрываемся, папку проекта можно
        # удалять. Grace нужен, чтобы не убить себя при первом же idle-цикле.
        # grace НЕ наследуем из окружения родителя (случайный TL_* в shell
        # мог превратить нормальный запуск в самоубийство); только --arg.
        grace = 45.0
        for i, a in enumerate(sys.argv):
            if a == "--orphan-grace" and i + 1 < len(sys.argv):
                grace = float(sys.argv[i + 1])
        last_seen = 0.0          # первый цикл обязан выполнить проверку,
        first_tick = True        # иначе grace отсчитывается от старта сервера
        dbg = os.environ.get("TL_WATCHDOG_DEBUG") == "1"
        def _log(msg):
            if dbg:
                print("[watchdog]", msg, flush=True)
        while True:
            time.sleep(2.0)
            confirmed = False
            if _pid_alive(ppid):
                confirmed = True
                if ppid != os.getppid():
                    try:   # если можем прочитать environ и маркера нет — это НЕ наш родитель
                        env = open(f"/proc/{ppid}/environ", "rb").read().split(b"\0")
                        if b"TL_GUI_APP=1" not in env:
                            confirmed = False
                    except OSError:
                        pass   # Windows/нет доступа — полагаемся на HTTP-подтверждение
            hb_age = None
            try:
                hb_age = time.time() - HB_PATH.stat().st_mtime
            except OSError:
                pass
            if hb_age is not None and hb_age < 15:
                confirmed = True   # GUI стучался недавно — хозяин жив
            _log(f"ppid={ppid} pidalive={_pid_alive(ppid)} hb_age="
                 f"{None if hb_age is None else round(hb_age)} confirmed={confirmed}")
            if confirmed or first_tick:
                last_seen = time.time()
                first_tick = False
            elif time.time() - last_seen > grace:
                print("[i] родительское приложение закрыто — сервер останавливается")
                LOCK_FILE.unlink(missing_ok=True)
                os._exit(0)

    threading.Thread(target=_watch_parent, daemon=True).start()
    try:
        app.run(host="127.0.0.1", port=chosen)
    finally:
        LOCK_FILE.unlink(missing_ok=True)         # освобождаем место для перезапуска
