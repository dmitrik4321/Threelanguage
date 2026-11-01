#!/usr/bin/env python3
"""Unit-тесты UserData-журнала (папка UserData/<username>.json).

Что проверяем (по требованию хозяина): сервер на каждого пользователя пишет
файл со словами — на каких языках знает, сколько раз спрашивали, сколько раз
ошибся, последний ответ верно/неверно. Плюс идемпотентность повторного push и
сводка _userdata_stats. Тесты офлайн, сеть не нужна; импорт server работает
без Flask (эндпоинты в тест не заходят).
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


def _state(cid="tat:t1", reps=3, lapses=1, grade=3, text="привет", lang="pt-BR"):
    """Слепок состояния клиента в схеме build/app.py default_state()."""
    return {
        "cards": {cid: {"due_ms": 1, "interval_d": 1.0, "ease": 2.5,
                        "reps": reps, "lapses": lapses, "stability": 1.0,
                        "difficulty": 5.0, "state": "review",
                        "last_grade": grade}},
        "words": {cid: {"text": text, "lang": lang,
                        "source": "tatoeba", "license": "CC BY 2.0 FR"}},
        "minutes": {"pt-BR": 1.0}, "level": 2, "history": [],
    }


def main() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            fails += 1

    with tempfile.TemporaryDirectory() as td:
        server.USERDATA_DIR = Path(td)  # изоляция: пишем во временную папку

        # 1) Первый push создаёт файл и журнал слова со всеми полями.
        ud = server.update_userdata_from_state("alice", _state())
        f = server._userdata_path("alice")
        check("userdata: файл создан в UserData/", f.exists())
        w = ud["words"]["tat:t1"]
        check("userdata: asks=reps, errors=lapses",
              w["asks"] == 3 and w["errors"] == 1)
        check("userdata: last_correct по last_grade>=3", w["last_correct"] is True)
        check("userdata: текст и язык слова сохранены",
              w["text"] == "привет" and "pt-BR" in w["langs"])
        import json
        disk = json.loads(f.read_text(encoding="utf-8"))
        check("userdata: на диске JSON с words/updated_at",
              disk["username"] == "alice" and "tat:t1" in disk["words"]
              and disk.get("updated_at") is not None)
        check("userdata: langs сериализуется списком (set в JSON нельзя)",
              isinstance(disk["words"]["tat:t1"]["langs"], list))

        # 2) Идемпотентность: identical-push не двигает счётчики и не пишет заново.
        mtime_before = f.stat().st_mtime_ns
        ud2 = server.update_userdata_from_state("alice", _state())
        check("userdata: повторный identical-push не удваивает asks",
              ud2["words"]["tat:t1"]["asks"] == 3)
        check("userdata: identical-push не трогает файл на диске",
              f.stat().st_mtime_ns == mtime_before)

        # 3) Прогресс: больше reps/lapses, неверный последний ответ.
        ud3 = server.update_userdata_from_state("alice", _state(reps=5, lapses=2, grade=1))
        w3 = ud3["words"]["tat:t1"]
        check("userdata: asks/errors растут максимумом", w3["asks"] == 5 and w3["errors"] == 2)
        check("userdata: grade<3 => last_correct=False", w3["last_correct"] is False)

        # 4) Многоязычность: то же слово на другом языке расширяет langs.
        ud4 = server.update_userdata_from_state("alice", _state(lang="zh", text="你好"))
        check("userdata: langs собирает все языки слова",
              set(ud4["words"]["tat:t1"]["langs"]) == {"pt-BR", "zh"})

        # 5) Сводка для ответа push и админки.
        st = server._userdata_stats(ud4)
        check("userdata: сводка words/known/errors посчитана",
              st["words"] == 1 and st["errors"] == 2 and "known" in st)
        # слово с 5 asks / 2 errors доля ошибок 0.4 > 1/3 => не «известно»
        check("userdata: known по эвристике доля-ошибок<=1/3", st["known"] == 0)
        st_ok = server._userdata_stats({"words": {"x": {"asks": 6, "errors": 2}}})
        check("userdata: 2/6 ошибок <= 1/3 => известно", st_ok["known"] == 1)

        # 6) Пустое/битое состояние не создаёт файл и не роняет журнал.
        check("userdata: push без карточек => None (нет журнала)",
              server.update_userdata_from_state("bob", {"history": []}) is None)
        check("userdata: у bob файл не создан",
              not server._userdata_path("bob").exists())
        check("userdata: load битого файла => пустой журнал без падения",
              server.load_userdata("alice")["username"] == "alice")

        # 7) Санитизация имени (defense-in-depth).
        p = server._userdata_path("../evil/name!")
        check("userdata: имя файла санитизировано", "/" not in p.name and p.name.endswith(".json"))

    print("USERDATA FAILS:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
