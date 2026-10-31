"""Клиент синхронизации с локальным сервером (local_server/server.py).

stdlib-only (urllib), чтобы десктоп-билд не требовал requests. Протокол —
ровно эндпоинты локального сервера: /api/auth/*, /api/sync/*, /api/stats.
Все сетевые ошибки возвращаются строкой, не исключением — UI живёт без сети.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:8765"
TIMEOUT = 4.0


class SyncClient:
    def __init__(self, base: str = DEFAULT_BASE):
        self.base = base.rstrip("/")
        self.token: str | None = None
        self.username: str | None = None

    # ---- низкоуровень
    def _req(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, json.loads(r.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode() or "{}")
            except Exception:
                payload = {"error": str(e)}
            return e.code, payload
        except Exception as e:                       # нет сети / сервер не запущен
            return 0, {"error": f"сеть недоступна: {e}"}

    # ---- auth
    def health(self) -> bool:
        st, _ = self._req("GET", "/health")
        return st == 200

    def register(self, username: str, password: str) -> tuple[bool, str]:
        st, j = self._req("POST", "/api/auth/register",
                          {"username": username, "password": password})
        if st == 200 and j.get("token"):
            self.token, self.username = j["token"], j["username"]
            return True, "аккаунт создан, токен получен"
        return False, j.get("error", f"HTTP {st}")

    def login(self, username: str, password: str) -> tuple[bool, str]:
        st, j = self._req("POST", "/api/auth/login",
                          {"username": username, "password": password})
        if st == 200 and j.get("token"):
            self.token, self.username = j["token"], j["username"]
            return True, f"вход выполнен: {j['username']}"
        return False, j.get("error", f"HTTP {st}")

    def logout(self):
        self.token = self.username = None

    # ---- sync
    def push_state(self, state: dict) -> tuple[bool, str]:
        st, j = self._req("POST", "/api/sync/push", state)
        return (st == 200 and j.get("ok")), j.get("error", f"{len(state)} ключей отправлено")

    def pull_state(self) -> tuple[dict | None, str]:
        st, j = self._req("GET", "/api/sync/pull")
        if st == 200:
            return j.get("state"), "получено"
        return None, j.get("error", f"HTTP {st}")

    def stats(self) -> tuple[dict | None, str]:
        st, j = self._req("GET", "/api/stats")
        return (j, "ok") if st == 200 else (None, j.get("error", f"HTTP {st}"))
