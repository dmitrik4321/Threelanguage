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

# Хост по умолчанию — 127.0.0.1 (петля): сервер принимает соединения только с
# этого ПК, наружу ничего не торчит. TL_SERVER_HOST=0.0.0.0 открывает сервер в
# LAN — для теста с телефона. ВНИМАНИЕ: вместе с /admin и sync/push наружу
# уходит всё; при открытии в сети ОБЯЗАТЕЛЬНО задай TL_ADMIN_TOKEN или смени
# пароль админки в admin_credentials.json (проверка и предупреждение в __main__).
HOST = os.environ.get("TL_SERVER_HOST", "127.0.0.1")

# --- LAN-режим на лету (команда в окно сервера) -------------------------------
# Раньше для теста с телефона приходилось останавливать сервер и перезапускать
# его с TL_SERVER_HOST=0.0.0.0 (плюс правка файрвола). Теперь можно просто
# набрать в консольном окне запущенного сервера:  lanmode=1   (вкл.)
# или  lanmode=0   (выкл., обратно только петля 127.0.0.1). Механика: отдельный
# поток-читатель ждёт строки со stdin; при команде он поднимает ВТОРОЙ экземпляр
# Flask-приложения на том же порту, но привязанный к 0.0.0.0 (все интерфейсы),
# в собственном потоке — текущий цикл не прерывается, десктоп-клиент ничего не
# замечает. Флаг _LAN_MODE отражает состояние; /api/server_info и подсказки в
# консоли его учитывают. Выключение просит ОС закрыть listener (best-effort:
# на Windows сокет чужого потока корректно не остановить, тогда LAN работает до
# перезапуска — это честно напечатано в ответе команды).
LAN_HOST = "0.0.0.0"          # только для env-режима при старте (TL_SERVER_HOST)
_LAN_MODE = {"on": False, "ip": None}
_LAN_SRV = None               # werkzeug server для shutdown из lanmode=0
_EXIT_FLAG = {"bye": False}   # команда shutdown из окна -> чистый выход main-потока
_RUN_DONE = {"done": False}   # app.run завершился сам (сигнал poke-потоку)
_LAN_READY = threading.Event()  # сигнализация bind-успеха для читателя команд


def _lan_admin_guard() -> bool:
    """Разрешаем ли открывать LAN: нужен НЕдефолтный пароль админки либо
    TL_ADMIN_TOKEN. Иначе /admin, регистрация и sync ушли бы всей Wi-Fi."""
    token = ADMIN_TOKEN or (ADMIN_PASSWORD if ADMIN_PASSWORD != ADMIN_DEFAULT_PASS else "")
    return bool(token)


def _serve_lan(ip: str, port: int):
    """Отдельный werkzeug-сервер в своём потоке, привязанный к КОНКРЕТНОМУ
    LAN-адресу этого хоста (не 0.0.0.0!). Причина найдена E2E-тестом: на Linux
    второй listener на том же порту невозможен даже с SO_REUSEPORT, если первый
    слушает 127.0.0.1 (bind 0.0.0.0 даёт EADDRINUSE). Привязка к 192.168.x.y
    адресов не пересекает — петля и LAN живут одновременно, десктоп-клиент
    ничего не замечает. На Windows такого разделения тоже нет — там путь один:
    рестарт с TL_SERVER_HOST=0.0.0.0 (сказано прямо в консоли)."""
    from werkzeug.serving import make_server
    global _LAN_SRV
    try:
        # ВАЖНО: у установленной версии werkzeug в сигнатуре make_server НЕТ
        # параметра request_queue_size (проверено inspect.signature) — прошлая
        # правка с ним падала TypeError на старте LAN-потока. Backlog здесь
        # не критичен: threaded=True принимает соединения пулом потоков.
        srv = make_server(ip, port, app, threaded=True)
        _LAN_SRV = srv
        # событие готовности ставим ДО serve_forever: слушатель уже создан и
        # забинден внутри make_server, читатель команд ждёт именно этот сигнал,
        # а не вслепую time.sleep (гонка на медленных машинах).
        _LAN_READY.set()
        srv.serve_forever()
    except OSError as e:
        print(f"[lan] не удалось слушать {ip}:{port}: {e}", flush=True)
    except Exception as e:
        # НЕ только OSError: при старте второго сервера вылетает и RuntimeError,
        # и системный трейсбек (поймано E2E) — поток обязан dying gracefully.
        print(f"[lan] слушатель упал при старте ({type(e).__name__}: {e}) — "
              f"остался режим 127.0.0.1", flush=True)
    finally:
        _LAN_READY.set()      # разбудить читатель даже при неудаче
        _LAN_MODE["on"] = False
        _LAN_MODE["ip"] = None
        _LAN_SRV = None
        print("[lan] LAN-слушатель остановлен.", flush=True)


def _console_commands(port: int):
    """Поток-читатель stdin: команды окна сервера (см. выше)."""
    for line in sys.stdin:
        cmd = line.strip().lower()
        if not cmd:
            continue
        if cmd in ("lanmode=1", "lan on", "lan"):
            if _LAN_MODE["on"]:
                print(f"[lan] уже включён: http://{_lan_ip()}:{port}", flush=True)
                continue
            if not _lan_admin_guard():
                print("[lan] ОТКАЗ: пароль админки дефолтный и TL_ADMIN_TOKEN не задан.\n"
                      "      Смени пароль в admin_credentials.json или запусти сервер\n"
                      "      с TL_ADMIN_TOKEN=... , затем повтори lanmode=1.", flush=True)
                continue
            if os.name == "nt":
                print("[lan] Windows: второй слушатель на том же порту невозможен.\n"
                      "      Штатный способ LAN: перезапусти сервер с TL_SERVER_HOST=0.0.0.0\n"
                      "      (и inbound-правило файрвола, см. README).", flush=True)
                continue
            ip = _lan_ip()
            if not ip or ip.startswith("127."):
                print("[lan] не нашёл LAN-адрес этого хоста — нет Wi-Fi/ETH? \n"
                      "      Перезапусти с TL_SERVER_HOST=0.0.0.0 как запасной путь.", flush=True)
                continue
            _LAN_READY.clear()
            t = threading.Thread(target=_serve_lan, args=(ip, port), daemon=True)
            t.start()
            ready = _LAN_READY.wait(timeout=10)
            if ready and _LAN_SRV is not None and t.is_alive():
                _LAN_MODE.update(on=True, ip=ip)  # флаг — только когда сервер реально поднялся
                print(f"[lan] ВКЛЮЧЁН. Телефон (тот же Wi-Fi): http://{_lan_ip()}:{port}\n"
                      f"      Не пускает? inbound-правило файрвола:\n"
                      f'      netsh advfirewall firewall add rule name="threelanguage-lan"'
                      f" dir=in action=allow protocol=TCP localport={port}", flush=True)
            else:
                print("[lan] слушатель не поднялся (порт занят?) — осталось 127.0.0.1", flush=True)
        elif cmd in ("lanmode=0", "lan off"):
            if not _LAN_MODE["on"]:
                print("[lan] и так выключен (только 127.0.0.1)", flush=True)
                continue
            # shutdown() запускаем в ОТДЕЛЬНОМ потоке: он дергает join() на
            # LAN-потоке, а читатель команд (мы) не должен ждать — иначе
            # последующие строки stdin (shutdown) встанут в очередь за join.
            # Флаг снимаем сразу: /api/server_info честно отражает состояние.
            srv = _LAN_SRV
            _LAN_MODE.update(on=False, ip=None)
            if srv is not None:
                threading.Thread(target=srv.shutdown, daemon=True).start()
            print("[lan] ВЫКЛЮЧЕН, снова только 127.0.0.1.", flush=True)
        elif cmd in ("help", "?"):
            print("[cmd] команды окна: lanmode=1 | lanmode=0 | help | shutdown", flush=True)
        elif cmd == "shutdown":
            # Только unlink + флаг; выход — за main-потоком (_exit_watcher).
            # srv.shutdown() отсюда НЕ вызывать: accept-loop LAN-потока сидит в
            # select на слушающем сокете, request_queue_size=1, и join из
            # shutdown() может взаимоблокироваться (поймано E2E: lanmode=0
            # работал, а shutdown после него висел 15с). os._exit из этого
            # потока тоже опасен — обрывает serve_forever с трейсбеком.
            _EXIT_FLAG["bye"] = True
            LOCK_FILE.unlink(missing_ok=True)
            print("[i] остановка по команде из окна…", flush=True)
        else:
            print(f"[cmd] неизвестная команда: {cmd!r} (help — список)", flush=True)


def _lan_ip() -> str:
    """Best-effort IPv4 этого хоста в LAN (для подсказок в консоли и API)."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))   # трафик не уходит, берёт src ip
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "?"

# ------------------------------------------------------------- UserData ----
# Папка UserData: на КАЖДОГО пользователя — свой JSON-файл <username>.json.
# Сервер пишет туда журнал по словам (word log): какие слова/фразы пользователь
# знает на каких языках, сколько раз слово спрашивали, сколько раз ошибся,
# какой был последний ответ (верно/неверно) и когда. Это «человекочитаемая»
# копия прогресса: states в SQLite — слепок клиента (машина), UserData —
# накопительный журнал (его можно открыть глазами, отдать тренеру, смержить
# с другого устройства). Писается при каждом успешном /api/sync/push из
# diff-состояния; TL_USERDATA_DIR перебивает путь (для тестов изоляции).
USERDATA_DIR = Path(os.environ.get(
    "TL_USERDATA_DIR", str(Path(__file__).resolve().parent.parent / "UserData")))


def _userdata_path(username: str) -> Path:
    """Путь к файлу пользователя; имя санитизируем — username уже lower-strip
    на register, но defense-in-depth: только [a-z0-9_.-], иначе '_'."""
    safe = re.sub(r"[^a-zA-Z0-9_.-]", "_", username)[:64] or "anon"
    return USERDATA_DIR / f"{safe}.json"


def _empty_userdata(username: str) -> dict:
    return {"username": username, "updated_at": None, "words": {}}


def load_userdata(username: str) -> dict:
    p = _userdata_path(username)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("words"), dict):
            # на диске langs хранится списком (JSON не умеет множества), а
            # _compute_userdata работает с set — восстанавливаем тип при загрузке
            for w in data["words"].values():
                if isinstance(w.get("langs"), list):
                    w["langs"] = set(w["langs"])
            return data
    except Exception:
        pass
    return _empty_userdata(username)


def update_userdata_from_state(username: str, state: dict) -> dict | None:
    """Точка входа из push(): считает журнал и, если есть изменения, пишет
    файл UserData/<username>.json. Возвращает журнал (или None, если карточек
    в состоянии нет)."""
    ud = _compute_userdata(username, state, load_userdata(username))
    if ud is None:
        return None
    # запись на диск только если счётки реально двинулись (_dirty ставит
    # _compute_userdata); повторный identical-push не трогает файл
    if ud.pop("_dirty", False):
        ud["updated_at"] = int(time.time())
        ud["level"] = state.get("level")
        ud["minutes"] = state.get("minutes", {})
        try:
            USERDATA_DIR.mkdir(parents=True, exist_ok=True)
            out = json.dumps(_userdata_serializable(ud), ensure_ascii=False, indent=2)
            path = _userdata_path(username)
            # атомарная запись: temp+os.replace. Если содержимое совпадает с
            # уже лежащим на диске — файл НЕ перезаписываем (identical-push
            # не двигает mtime; os.replace всегда обновляет мету времени)
            try:
                if path.read_text(encoding="utf-8") == out:
                    return ud
            except OSError:
                pass
            tmp = path.with_suffix(".tmp")
            tmp.write_text(out, encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass  # журнал — best effort, sync push от него не зависит
    return ud


def _compute_userdata(username: str, state: dict, base: dict | None = None) -> dict | None:
    """Чистая функция: обновляет журнал-словарь по пушнутому состоянию клиента
    (ничего не пишет на диск; флаг "_dirty" говорит, изменилось ли что-то).

    state — схема build/app.py default_state(): cards{cid -> SRS-стейт},
    minutes{lang}, history[], level... Из неё нам нужны карточки: cid несёт
    стабильный id фразы ("tat:<hash>" из data_sources), а текст фразы живёт
    в words {cid -> {text, lang, source, license}} того же состояния (перед
    push клиент докладывает туда данные каждой показанной карточки — так
    сервер понимает, ЧТО именно за слово стоит за хэшем, без обращения к
    внешним API).

    Для каждой карточки в журнале:
      asks   — сколько раз слово вообще спрашивали (reps в SRS);
      errors — сколько раз ошиблись (lapses — накопленные провалы судит ядро);
      last_correct — последний ответ верно/неверно (grade>=3 => верно);
      langs  — на каких языках пользователь это слово знает (lang карточки);
      text/source/license — сама фраза и происхождение (CC-BY атрибуция).
    Фолбэк без слов: если words[cid] нет (клиент старой версии), пишем
    запись с text=null — счётчики всё равно корректны."""
    cards = state.get("cards")
    if not isinstance(cards, dict) or not cards:
        return None
    words_meta = state.get("words") if isinstance(state.get("words"), dict) else {}
    ud = base if base is not None else _empty_userdata(username)
    ud.setdefault("words", {})
    dirty = False
    for cid, c in cards.items():
        if not isinstance(c, dict):
            continue
        meta = words_meta.get(cid) or {}
        w = ud["words"].get(cid)
        if w is None:
            w = {"text": meta.get("text"), "lang": meta.get("lang"),
                 "langs": set([meta.get("lang")]) if meta.get("lang") else set(),
                 "asks": 0, "errors": 0, "last_correct": None, "last_ts": None}
            ud["words"][cid] = w
            dirty = True
        # defense-in-depth: журнал, сохранённый старой версией сервера, мог
        # лежать на диске с langs-списком — приводим к set до первого .add()
        if not isinstance(w.get("langs"), set):
            w["langs"] = set(w.get("langs") or [])
        # накапливаемые поля берём максимумом: push приходит полным слепком,
        # повторный push того же состояния не должен удваивать счётчики
        reps = int(c.get("reps") or 0)
        lapses = int(c.get("lapses") or 0)
        if reps > (w.get("asks") or 0):
            w["asks"] = reps
            dirty = True
        if lapses > (w.get("errors") or 0):
            w["errors"] = lapses
            dirty = True
        # последний ответ: грейд в SRS-стейте не хранится напрямую — клиент
        # кладёт в слепок поле last_grade (1..4) для cid
        lg = c.get("last_grade")
        if lg is not None and int(lg) != w.get("_last_grade_raw"):
            w["last_correct"] = bool(int(lg) >= 3)   # Again=1 Hard=2 Good=3 Easy=4
            w["_last_grade_raw"] = int(lg)
            w["last_ts"] = int(time.time())
            dirty = True
        if meta.get("lang"):
            before = len(w["langs"])
            w["langs"].add(meta["lang"])
            dirty = dirty or len(w["langs"]) != before
        if meta.get("text") and w.get("text") != meta["text"]:
            w["text"] = meta["text"]
            w["source"], w["license"] = meta.get("source"), meta.get("license")
            dirty = True
    ud["_dirty"] = dirty
    return ud


def _userdata_serializable(ud: dict) -> dict:
    """set -> sorted list (JSON не умеет множества), служебное _last_grade_raw
    наружу не отдаём."""
    o = dict(ud)
    ws = {}
    for cid, w in ud["words"].items():
        w2 = {k: v for k, v in w.items() if not k.startswith("_")}
        w2["langs"] = sorted(w.get("langs", []))
        ws[cid] = w2
    o["words"] = ws
    return o


def _userdata_stats(ud: dict | None) -> dict:
    """Сводка журнала для ответа push и админки: сколько слов в работе,
    сколько «известно» (отвечали верно >=2 раз и ни одного незакрытого провала),
    сколько ошибок суммарно. Считается от счётков asks/errors/last_correct —
    тех самых полей, которые просил хозяин."""
    if not ud:
        return {"words": 0, "known": 0, "errors": 0}
    known = 0
    errors = 0
    for w in ud.get("words", {}).values():
        a = int(w.get("asks") or 0)
        e = int(w.get("errors") or 0)
        errors += e
        if a >= 2 and ud_ok_word(a, e):
            known += 1
    return {"words": len(ud.get("words", {})), "known": known, "errors": errors}


def ud_ok_word(asks: int, errors: int) -> bool:
    """Слово считается «известным», если спрашивали >=2 раза и доля ошибок
    не больше трети (эвристика журнала; SRS-решение принимает ядро, не журнал)."""
    return errors * 3 <= asks
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
    # Уточнение для веба. Приоритет сигналов:
    #   1) явный заголовок X-Client из fetch (страница знает, какая она);
    #   2) cookie tl_served, поставленный последней отдачей страницы;
    #   3) свежая эвристика по User-Agent (если cookie нет).
    if kind == "web":
        if h == "web-mobile":                       # fetch из мобильной сборки
            kind = "web-mobile"
        elif request.cookies.get("tl_served") == "web-mobile":
            kind = "web-mobile"
        elif not request.cookies.get("tl_served") and _is_mobile_ua(request.headers.get("User-Agent", "")):
            kind = "web-mobile"
        elif h == "web" and request.cookies.get("tl_served") == "web-mobile":
            pass                                   # страница authoritative: см. set_cookie в web_index
        elif h == "web" and _is_mobile_ua(request.headers.get("User-Agent", "")) and \
                request.cookies.get("tl_served") != "web":
            kind = "web-mobile"                    # мобильный браузер без разметки -> мобилка
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
    # tl_served всегда перезаписывается по текущему решению: страница — authoritative,
    # stale-cookie от прошлой сессии не должен переживать смену устройства/браузера.
    resp.set_cookie("tl_served", "web-mobile" if serve_mobile else "web", max_age=3600, samesite="Lax")
    resp.vary.add("User-Agent")   # корректный кэш: разная выдача по UA
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


@app.get("/api/server_info")
def api_server_info():
    """Что видит клиент (десктоп/веб): фактический хост и LAN-адрес сервера.

    Для теста с телефона: открой url из ответа на том же Wi-Fi. Поля host/lan_ip
    помогают понять, почему 127.0.0.1 не пускаетother девайс — сервер слушает петлю."""
    lan_open = (_LAN_MODE["on"] or
                HOST not in ("127.0.0.1", "localhost"))
    return jsonify({"app": "threelanguage-local", "host": HOST, "port": PORT,
                    "lan_mode": lan_open,
                    "lan_ip": _lan_ip() if lan_open else None,
                    "url": (f"http://{_lan_ip()}:{PORT}" if lan_open
                            else f"http://{HOST}:{PORT}"),
                    "hint": ("сервер доступен только с этого ПК (127.0.0.1). "
                             "Для телефона набери в окне сервера: lanmode=1 "
                             "(или перезапусти с TL_SERVER_HOST=0.0.0.0)"
                             if not lan_open
                             else f"открой http://{_lan_ip()}:{PORT} с телефона в том же Wi-Fi")})


@app.get("/api/content/lesson")
def content_lesson():
    """Живой урок из того же модуля, что использует десктоп (build/data_sources.py)."""
    langs = (request.args.get("langs") or "en,pt-BR,zh").split(",")
    sys.path.insert(0, str(REPO_ROOT / "build"))
    try:
        import data_sources as ds
        # cached_build_lesson: кэш собранной колоды (TTL 1 час) + параллельные
        # запросы к API — иначе первый холодный вызов вешает веб-клиент на
        # десятки секунд (последовательные HTTP к Tatoeba с timeout 6с).
        lesson = ds.cached_build_lesson([l.strip() for l in langs])
    except Exception as e:
        return jsonify({"error": f"content: {e}"}), 502
    return jsonify(lesson)


@app.get("/api/content/placement")
def content_placement():
    lang = request.args.get("lang") or "pt-BR"
    # langs — список языков урока: из них берутся дистракторы-переводы
    # (варианты должны быть на языке, ОТЛИЧНОМ от языка prompt — инвариант
    # «отвечаемого вопроса», см. build/data_sources.placement_items).
    langs = [x.strip() for x in (request.args.get("langs") or lang).split(",") if x.strip()]
    if lang not in langs:
        langs.insert(0, lang)
    sys.path.insert(0, str(REPO_ROOT / "build"))
    try:
        import data_sources as ds
        # кэш + параллельные запросы (см. content_lesson выше)
        items = ds.cached_placement_items(langs)
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
        # Миграция: в старых БД (до PR с логами статуса) колонки status нет —
        # INSERT(...status) падал молча (except sqlite3.Error), и логи событий
        # не писались вообще. Добавляем колонку, если её отсутствует.
        cols = {r["name"] for r in con.execute("PRAGMA table_info(events)")}
        if "status" not in cols:
            con.execute("ALTER TABLE events ADD COLUMN status INTEGER NOT NULL DEFAULT 200")


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
    # UserData-журнал: человекочитаемая копия прогресса по словам
    # (<папка>/UserData/<username>.json). Best effort — ошибка журнала не
    # должна ломать синхронизацию клиента.
    ud_stats = None
    try:
        ud = update_userdata_from_state(u["username"], body)
        if ud:
            ud_stats = _userdata_stats(ud) | {"file": str(_userdata_path(u["username"]))}
    except Exception as e:
        app.logger.warning("userdata update failed: %s", e)
    resp = {"ok": True, "bytes": len(json.dumps(body))}
    if ud_stats:
        resp["userdata"] = ud_stats
    return jsonify(resp)


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


def _free_port(preferred: int, host: str = "127.0.0.1") -> int:
    """Первый свободный TCP-порт на host, начиная с preferred."""
    import socket
    for p in [preferred] + [preferred + i for i in range(1, 20)]:
        s = socket.socket()
        try:
            s.bind((host, p))
            return p
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit(f"Не нашёл свободный порт рядом с {preferred}")


# ------------------------------------------------------------------ banner --
# Цветной ASCII-дракон под строкой «Нажмите Ctrl+C для выхода». Flask сам
# печатает "Press CTRL+C to quit" (EN) / русскую версию на Windows — мы не
# трогаем его вывод, а рисуем свой баннер ПОВЕРХ: ANSI escape-коды работают в
# cmd.exe/PowerShell (Windows 10+), Terminal и Linux/macOS. Где ANSI не
# поддерживается (старые консоли) — print_ansi тихо отключается и дракон
# остаётся монохромным: украшение не должно ломать утилитарный вывод сервера.
DRAGON_BANNER = r"""
                     )kk88)88)88kk)
                     )DDAA?AA?AADD)
        )PP)) )) ))PP)AAAAAAAAAAAA)PP))) )) )PP)
        )kk88)88)88kk)AAr`rAAr`rAA)kk)88)88)8kk)
        )AAAAAAAAAAAA)AArrrAArrrAA)AAAAAAAAAAAA)
        )AArrrAArrrAA)AAA))))))AAA)AAArrrAArrrA)
        )AArrrAArrrAA)AAAA)AA)AAAA)AAArrrAArrrA)
        )AA,,,,,,AAAA)AAAAAAAAAAAA)AAAA,,,,,,AA)
        )AAA4AA4AAAAA))))AAAAAA))))AAAAA4AA4AAA)
        )AAAAAAAAAAAA)  )AAAAAA)  )AAAAAAAAAAAA)
         ))))8888A)))   )AAAAAA)   )))A8888))))
            )AAAAA)     )AAAAAA)     )AAAAA)
            )AAAAA)))))))AAAAAA)))))))AAAAA)
            )AAAAAAAAAAAAAAAAAAAAAAAAAAAAAA)    ((((5(
            )AAAAATTTTTTTTTTTTTTTTTTTTAAAAA)))))AAAAA)
            )AAAAAiiiiiiiiiiiiiiiiiiiiAAAAAAAAAAAA>>>(
            )AAAAAiiiiiiiiiiiiiiiiiiiiAAAAAAAAAAAA)
            )AAAAAiiiiiiiiiiiiiiiiiiiiAAAAA)))))))
            )AAAAAiiiiiiiiiiiiiiiiiiiiAAAAA)
            (/,,,,,,,,,,,,,,,,,,,,,,,,,,,,/(
               )AAAAAA)          )AAAAAA)
               )AAAAAA)          )AAAAAA)
               )AAAAAA)          )AAAAAA)
""".strip("\n")

# цвет каждого символа пиксель-арта: A=чешуя(красный), k=тёмно-красная окантовка,
# P=шип(белый), D=глаз(жёлтый), ?=зрачок(чёрный), r=нотрицательная морда(оранж),
# ,=брюхо(светло-красный), 4=ноздри, 8=теневые блоки, T/i=лапы, 5=>>=язык
_DRAGON_COLORS = {
    "A": "\033[38;5;167m",   # приглушённый красный (основная чешуя)
    "k": "\033[38;5;88m",    # тёмный красный (контур головы/тела)
    "P": "\033[97m",         # белый (шипы)
    "D": "\033[93m",         # жёлтый (глаза)
    "?": "\033[38;5;16m",    # почти чёрный (зрачки)
    "r": "\033[38;5;209m",   # лосось (морда)
    ",": "\033[38;5;210m",   # розовато-красный (брюшко)
    "4": "\033[38;5;94m",    # коричневый (ноздри)
    "8": "\033[38;5;131m",   # пыльно-красный (тени)
    "T": "\033[38;5;168m",   # лапы светлее тела
    "i": "\033[38;5;168m",
    "(": "\033[38;5;88m", ")": "\033[38;5;88m", "/": "\033[38;5;88m",
    "5": "\033[38;5;202m", ">": "\033[38;5;203m",  # огонь/язык — оранжевый
    "`": "\033[38;5;210m",
}


def _supports_ansi() -> bool:
    """Рисуем цвет только там, где его понимают. env NO_COLOR — общий стандарт
    («не крась»), TERM=dumb — старые терминалы; в pipe (лог-файл) коды мусорят."""
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    term = os.environ.get("TERM", "")
    if term in ("", "dumb") and os.name != "nt":
        return False
    return True


def print_dragon_banner():
    """Двухпроходный рендер: сначала проверка поддержки ANSI, потом печать."""
    try:
        lines = DRAGON_BANNER.splitlines()
        if _supports_ansi():
            out = []
            for ln in lines:
                buf = []
                for ch in ln:
                    c = _DRAGON_COLORS.get(ch)
                    buf.append((c + ch + "\033[0m") if c else ch)
                out.append("".join(buf))
            text = "\n".join(out)
        else:
            text = "\n".join(lines)
        print(text, flush=True)
    except Exception:
        pass   # баннер — украшение; падать из-за него сервер не имеет права


_ANSI_ON_WINDOWS_DONE = False


def _enable_ansi_on_windows():
    """Windows 10+ cmd/PowerShell понимают ANSI, но по умолчанию режим VT
    выключен — включаем через kernel32.SetConsoleMode(ENABLE_VIRTUAL_TERMINAL_
    PROCESSING). Если импортирован colorama — его init() делает то же самое,
    используем что есть. Идемпотентно; на Linux/macOS no-op; при любой неудаче
    просто молча остаёмся монохромными (баннер от этого не перестаёт печататься)."""
    global _ANSI_ON_WINDOWS_DONE
    if _ANSI_ON_WINDOWS_DONE or os.name != "nt":
        return
    _ANSI_ON_WINDOWS_DONE = True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        for std in (-11, -12):   # stdout, stderr
            h = k32.GetStdHandle(std)
            mode = ctypes.c_ulong(0)
            if k32.GetConsoleMode(h, ctypes.byref(mode)):
                k32.SetConsoleMode(h, mode.value | 0x0004)
    except Exception:
        pass
    try:
        import colorama
        colorama.just_fix_windows_console() if hasattr(colorama, "just_fix_windows_console") else colorama.init()
    except Exception:
        pass


_BANNER_SHOWN = {"done": False}


def _show_banner_once():
    """Печатаем дракона ровно один раз за процесс (и в stdin-режиме, и после
    poke-выхода не повторяем)."""
    if _BANNER_SHOWN["done"]:
        return
    _BANNER_SHOWN["done"] = True
    _enable_ansi_on_windows()
    print_dragon_banner()


def _banner_hook():
    """Хук первого соединения + fallback-таймер.

    Порядок строк в окне сервера должен быть: *Running on…* → *Press CTRL+C to
    quit* → ДРАКОН. Но werkzeug печатает «Press CTRL+C» АСИНХРОННО: свой поток
    report_startup_messages стартует внутри make_server/run_simple и может
    выдать строку ПОЗЖЕ нашей печати (поймано E2E: баннер залезал между
    «Running on» и «Press CTRL+C»). Поэтому баннер рисуем не до app.run, а
    когда точно знаем, что запуск состоялся: первый входящий запрос (хук
    before_request ниже) или истечение fallback-таймера (на случай сервера без
    трафика). К этому моменту startup-сообщения werkzeug уже в буфере stdout —
    порядок гарантирован.»"""
    _show_banner_once()


_FALLBACK_TIMER = threading.Timer(2.5, _banner_hook)


@app.before_request
def _banner_first_connection():
    """Первое соединение с сервером = верный признак, что слушатель живой и
    сообщения запуска напечатаны. Дальше хук бесполезен — снимаем себя."""
    if not _BANNER_SHOWN["done"]:
        _banner_hook()
        try:
            _FALLBACK_TIMER.cancel()
        except Exception:
            pass


def run_server_forever(host: str, port: int):
    """app.run с перехватом Ctrl+C: Flask сам обработает KeyboardInterrupt и
    выйдет штатно (сработает finally с unlink lock). Баннер-дракон печатается
    НЕ здесь, а по хуку первого соединения / fallback-таймеру (см.
    _banner_hook — почему: werkzeug пишет своё «Press CTRL+C» позже нашего
    print, если рисовать до app.run). Прощание — после выхода.
    В потоке-обёртке не вызываем — только main."""
    _FALLBACK_TIMER.daemon = True
    _FALLBACK_TIMER.start()
    try:
        app.run(host=host, port=port)
    except KeyboardInterrupt:
        print("\n[i] остановлено пользователем (Ctrl+C). Пока!", flush=True)
    finally:
        _show_banner_once()   # даже при раннем падении: хоть что-то видно


if __name__ == "__main__":
    # Порт: TL_SERVER_PORT или 8765; если занят чужим процессом — автовыбор
    # следующего свободного + запись фактического порта в ~/.threelanguage/server_port,
    # чтобы десктоп-клиент сам нашёл сервер (см. build/sync_client.py).
    # Для LAN (тест с телефона): TL_SERVER_HOST=0.0.0.0 TL_SERVER_PORT=8765;
    # порт фиксируй явно — файл server_port лежит на ПК, с телефона его не видно.
    # На Windows добавь inbound-правило файрвола:
    #   netsh advfirewall firewall add rule name="threelanguage-lan" dir=in action=allow protocol=TCP localport=8765
    if HOST not in ("127.0.0.1", "localhost"):
        token = ADMIN_TOKEN or (ADMIN_PASSWORD if ADMIN_PASSWORD != ADMIN_DEFAULT_PASS else "")
        if not token:
            print("[!!] ВНИМАНИЕ: сервер открыт в сеть (TL_SERVER_HOST=%s), но TL_ADMIN_TOKEN "
                  "не задан, а пароль админки — дефолтный из admin_credentials.json.\n"
                  "     Любое устройство в твоей Wi-Fi сможет зайти в /admin. Задай "
                  "TL_ADMIN_TOKEN или смени пароль, либо верни 127.0.0.1." % HOST)
    acquire_lock()
    # Фактический порт выбираем ДО запуска читателя команд — подсказки в окне
    # (и ответ lanmode=1) должны показывать именно его, а не желаемый.
    chosen = _free_port(PORT, HOST if HOST != "0.0.0.0" else "127.0.0.1")
    if chosen != PORT:
        print(f"[!] Порт {PORT} занят — использую {chosen}")
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(chosen))
    print(f"Threelanguage local server: http://{HOST}:{chosen}  "
          f"(админка: http://{HOST}:{chosen}/admin)")
    if HOST not in ("127.0.0.1", "localhost"):
        print(f"[i] Доступ из сети: http://{_lan_ip()}:{chosen}  "
              f"(если не пускает — проверь файрвол и совпадение Wi-Fi)")

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
    # Читатель команд окна сервера (lanmode=1 | lanmode=0 | help | shutdown).
    # stdin может быть закрыт/неперенаправлен (запуск из GUI через Popen без
    # PIPE) — поток это честно переживает и завершается.
    try:
        threading.Thread(target=_console_commands, args=(chosen,),
                         daemon=True).start()
        print('[i] команды окна сервера: lanmode=1 | lanmode=0 | help | shutdown')
    except Exception:
        pass

    def _poke_loop():
        """Команда shutdown ставит флаг. Раньше здесь был единственный poke
        /health «в надежде», что main-поток проснётся между accept-циклами —
        E2E показал: app.run сидит в select и не просыпается никогда, процесс
        висел мёртвым грузом. Новая схема надёжна: после poke ждём реально
        завершившийся app.run (флаг _RUN_DONE) ~5 секунд; если не дождались —
        осознанный os._exit(0): все потоки сервера daemon, lock уже снят
        читателем команд, данные на диске записаны атомарно. Терять при этом
        нечего, а «заседающий python» — терять нельзя."""
        import urllib.request
        while not _EXIT_FLAG["bye"]:
            time.sleep(0.3)
        try:   # первый удар по loopback (основной слушатель)
            urllib.request.urlopen(f"http://127.0.0.1:{chosen}/health", timeout=2).read()
        except Exception:
            pass
        deadline = time.time() + 5.0
        while time.time() < deadline and not _RUN_DONE["done"]:
            time.sleep(0.2)
            try:   # повторные тычки: вдруг первый запрос утонул в очереди
                urllib.request.urlopen(f"http://127.0.0.1:{chosen}/health", timeout=1).read()
            except Exception:
                break   # сокет закрыт — скорее всего app.run уже вышел
        if not _RUN_DONE["done"]:
            print("[i] основной цикл не проснулся — принудительный выход", flush=True)
            LOCK_FILE.unlink(missing_ok=True)
            os._exit(0)
    threading.Thread(target=_poke_loop, daemon=True).start()
    try:
        run_server_forever(HOST, chosen)   # баннер-дракон + штатный выход по Ctrl+C
    finally:
        _RUN_DONE["done"] = True                   # poke-поток узнает, что мы вышли сам
        LOCK_FILE.unlink(missing_ok=True)          # освобождаем место для перезапуска
