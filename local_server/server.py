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

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path

try:
    from flask import Flask, jsonify, request, render_template_string
except ImportError as e:  # pragma: no cover
    raise SystemExit("Нужен Flask: pip install flask") from e

DB_PATH = Path.home() / ".threelanguage" / "local_server.db"
PORT_FILE = Path.home() / ".threelanguage" / "server_port"
PORT = int(os.environ.get("TL_SERVER_PORT", "8765"))
ADMIN_TOKEN = os.environ.get("TL_ADMIN_TOKEN", "")  # пусто = без токена (localhost)

app = Flask(__name__)


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
    if ADMIN_TOKEN and request.headers.get("X-Admin-Token") != ADMIN_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    return None


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
    if err:
        return err
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
    return render_template_string(ADMIN_TMPL, users=users, states=states, example=example)


@app.get("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time()), "app": "threelanguage-local"})


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
    chosen = _free_port(PORT)
    if chosen != PORT:
        print(f"[!] Порт {PORT} занят — использую {chosen}")
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(chosen))
    print(f"Threelanguage local server: http://127.0.0.1:{chosen}  "
          f"(админка: http://127.0.0.1:{chosen}/admin)")
    app.run(host="127.0.0.1", port=chosen)
