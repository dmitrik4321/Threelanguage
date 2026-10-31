"""Живые свободные источники контента вместо заглушек (docs/DATA_SOURCES.md).

Выбор источников — только полностью свободные и бесплатные API, без ключей:

  * Tatoeba REST API v0  (https://tatoeba.org/en/api_v0/search)
      Лицензия фраз: CC BY 2.0 FR (поле license в ответе). Бесплатно, без регистрации.
      Даёт пары «предложение на языке A → перевод на язык B» с транскрипциями.
      Покрывает связки en↔pt, en↔zh, pt↔zh и др.
  * Wiktionary REST / MediaWiki API (en.wiktionary.org)
      Лицензия текста: CC BY-SA. Бесплатно, без ключей.
      Даёт определения/переводы слов для подсказок и дистракторов.

Оффлайн-колоды (core vocab), которые идут в ход при недоступности сети или
когда API не покрывает пару (например, ru↔pt у Tatoeba почти нет):
файл data/vocab.json в формате deck_format.json.

Модуль кэширует ответы в ~/.threelanguage/content_cache.json (TTL 7 дней),
чтобы приложение не долбило API при каждом запуске и работало офлайн.
"""
from __future__ import annotations

"""Живые источники учебного контента Threelanguage (полностью свободные API, без ключей).

Tatoeba REST (CC-BY): переводы фраз между языками -> CHOICE_T2R/TYPING задания.
Wiktionary REST (CC-BY-SA): определения слов -> GLOSS задания.
Кэш ~/.threelanguage/content_cache.json (TTL 7 дней); без сети — офлайн-фолбэк.
Сигнатура уроков совместима с fixtures reference.py: id/title/languages/tasks[].
"""

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_PATH = Path.home() / ".threelanguage" / "content_cache.json"
CACHE_TTL = 7 * 86400
UA = {"User-Agent": "ThreelanguageDesktop/0.1 (learning app; contact: local)"}

# Коды языков приложения -> коды ISO в Tatoeba
TATOEBA_LANG = {"en": "eng", "pt-BR": "por", "zh": "cmn", "ru": "rus"}


def _cache_load() -> dict:
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_save(cache: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # кэш — best effort


def _cached_get(url: str) -> dict | list | None:
    """HTTP GET с disk-кэшем. Возвращает None при любой сетевой ошибке."""
    cache = _cache_load()
    ent = cache.get(url)
    if ent and time.time() - ent["ts"] < CACHE_TTL:
        return ent["data"]
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception:
        return ent["data"] if ent else None   # сеть легла — отдаём старый кэш
    cache[url] = {"ts": int(time.time()), "data": data}
    _cache_save(cache)
    return data


# --------------------------------------------------------------- Tatoeba ----
def tatoeba_pairs(source: str, target: str, query: str = "", limit: int = 10) -> list[dict]:
    """Фразы source→target c переводами. Пустой список если API недоступен.

    Каждая пара: {src, tgt, src_lang, tgt_lang, transcriptions, license}.
    """
    f, t = TATOEBA_LANG.get(source, source), TATOEBA_LANG.get(target, target)
    url = ("https://tatoeba.org/en/api_v0/search?from=%s&to=%s&sort=relevance&per_dir_limit=3"
           % (f, t) + ("&query=%s" % urllib.parse.quote(query) if query else ""))
    data = _cached_get(url)
    out = []
    for res in (data or {}).get("results", [])[:limit] if isinstance(data, dict) else []:
        groups = res.get("translations", [])
        flat = [g[0] if isinstance(g, list) and g else g for g in groups]
        tr = next((x for x in flat if isinstance(x, dict) and x.get("lang") == t
                   and x.get("isDirect", True)), None)
        if not tr:
            continue
        # транскрипции (pinyin для zh) лежат на самом предложении-переводе
        trans = [x.get("text", "") for x in (tr.get("transcriptions") or [])
                 if isinstance(x, dict)]
        out.append({
            "src": res["text"], "tgt": tr["text"],
            "src_lang": source, "tgt_lang": target,
            "transcriptions": trans[:1],
            "license": res.get("license", "CC BY"),
        })
    return out


# ------------------------------------------------------------ Wiktionary ----
def wiktionary_gloss(word: str, lang_code: str = "en") -> str | None:
    """Первое короткое определение слова из en.Wiktionary (CC BY-SA)."""
    url = ("https://en.wiktionary.org/api/rest_v1/page/definition/"
           + urllib.parse.quote(word))
    data = _cached_get(url)
    if not isinstance(data, dict):
        return None
    for entry in data.get(lang_code, []) or []:
        for d in entry.get("definitions", []) or []:
            g = (d.get("definition") or "").strip()
            if g:
                return g.split(";")[0][:90]
    return None


# --------------------------------------------------- колода уроков из API ----
# Слова-ядра, по которым строим карточки через Tatoeba (короткие фразы лучше
# учебных строк: дают естественный контекст). Отобраны частотные темы.
CORE_WORDS = ["hello", "goodbye", "thank you", "water", "cat", "dog", "house",
              "book", "friend", "today", "work", "food", "love", "time", "city"]

LEVEL_BY_LEN = lambda s: max(1, min(6, len(s.split()) + (2 if any(ord(c) > 0x2E80 for c in s) else 1)))


def build_lesson(langs: list[str]) -> dict:
    """Собирает урок из живых источников. При недоступности API возвращает
    offline-колоду data/vocab.json (или встроенную заглушку content.LESSON).

    Ядро (shared/algorithm/reference.py) не меняется — карточки приходят в
    тех же форматах kind, что принимали заглушки."""
    tasks, seen = [], set()
    for lang in langs:
        got = 0
        for w in CORE_WORDS:
            if got >= 5:
                break
            pairs = tatoeba_pairs("en", lang, query=w, limit=3)
            for p in pairs:
                key = _clean(p["tgt"])
                if key in seen or len(p["src"].split()) > 8 or len(p["src"]) < 4:
                    continue
                seen.add(key)
                tasks.append(_task_from_pair(lang, p))
                got += 1
                if got >= 5:
                    break
    if not tasks:
        return _offline_fallback(langs)
    return {"id": "live", "title": "Живой урок · Tatoeba (CC BY)",
            "languages": langs, "tasks": _mix_kinds(tasks)}


def _task_from_pair(lang: str, p: dict) -> dict:
    """Пара Tatoeba -> карточка. Для zh показываем транслитерацию (pinyin),
    только если она реально отличается от текста (иначе это дубль иероглифов)."""
    base = {"kind": "CHOICE_T2T", "lang": lang, "level": LEVEL_BY_LEN(p["src"]),
            "source": "tatoeba", "license": p["license"]}
    tr = (p.get("transcriptions") or [""])[0]
    hint = ("  (%s)" % tr) if tr and _clean(tr) != _clean(p["tgt"]) else ""
    t = dict(base, prompt="Как переводится «%s»%s?" % (p["tgt"], hint))
    t["_answer_en"] = p["src"]          # служебное: для генерации опций ниже
    return t


def _clean(s: str) -> str:
    """Нормализация фразы для дистракторов: без кавычек, знаков конца, пробелов-подряд."""
    import re as _re
    s = _re.sub(r"[“”\"«»]", "", s)
    return _re.sub(r"\s+", " ", s.strip().rstrip(".!?。！？…")).lower()


def _mix_kinds(tasks: list[dict]) -> list[dict]:
    """Превращает сырые CHOICE-заготовки в разные типы заданий ядра:
    CHOICE_T2T (варианты из других пар этого же урока), TYPE_EN2RU (ввод).
    Дистракторы берём из ответов соседних карточек — бесплатно и осмысленно.
    Опция-дубликат по нормализованной форме отбрасывается (иначе «Hello?»/
    «Hello.»/«Hello!» дают три одинаковых на вид варианта с одним correct)."""
    finals = []
    for i, t in enumerate(tasks):
        ans = t.pop("_answer_en")
        na = _clean(ans)
        cand = [x.get("_answer_en") or x.get("answer_ru") for x in tasks if x is not t]
        distr, used = [], {na}
        for o in cand:
            if o and len(o) <= 40 and _clean(o) not in used:
                distr.append(o); used.add(_clean(o))
            if len(distr) == 2:
                break
        if len(distr) >= 2:
            opts = [ans] + distr
            opts = [opts[(i + k) % 3] for k in range(3)]   # детермин. перемешивание
            t["options"], t["answer"] = opts, opts.index(ans)
            finals.append(t)
        elif lang_of_ru(t["lang"]) == "ru":
            finals.append(dict(kind="TYPE_EN2RU", lang=t["lang"], level=t["level"],
                               prompt="Переведи на русский: %s" % ans,
                               expected=[OFFLINE_GLOSS.get(na, ans)],
                               source="tatoeba", license=t["license"]))
        else:
            # нет русской опоры для typing — оставляем choice с тем, что есть
            opts = [ans] + distr
            t["options"], t["answer"] = opts, 0
            finals.append(t)
    return finals


def lang_of_ru(lang: str) -> str:
    """Есть ли у языка русская подсказка в OFFLINE_GLOSS-цепочке (для TYPE_*2RU)."""
    return "ru"  # урок строится вокруг ru-подсказок; реальный UI-язык см. packs.py


# --------------------------------------------- офлайн-колода (fallback) ----
OFFLINE_GLOSS = {  # мини-глоссарий RU, только чтобы typing-карточки судить ядром
    "hello": "привет", "goodbye": "пока", "thank you": "спасибо", "water": "вода",
    "cat": "кошка", "dog": "собака", "house": "дом", "book": "книга",
    "friend": "друг", "today": "сегодня", "work": "работа", "food": "еда",
    "love": "любовь", "time": "время", "city": "город",
}


def _offline_fallback(langs: list[str]) -> dict:
    """data/vocab.json (deck_format.json) или встроенная колода content.LESSON."""
    vp = Path(__file__).resolve().parent / "data" / "vocab.json"
    if vp.exists():
        try:
            d = json.loads(vp.read_text(encoding="utf-8"))
            tasks = [t for t in d.get("cards", []) if t.get("lang") in langs]
            if tasks:
                return {"id": d.get("id", "vocab"), "title": d.get("title", "Колода"),
                        "languages": langs, "tasks": tasks}
        except Exception:
            pass
    import content
    return content.LESSON


def placement_items(langs: list[str]) -> list[dict]:
    """Placement-банк: фразы разного уровня из Tatoeba (level 1..6 по длине).
    Если API недоступен — content.PLACEMENT_ITEMS (заглушка)."""
    items, seen = [], set()
    for lang in langs[:1] or ["en"]:
        for q in CORE_WORDS:
            for p in tatoeba_pairs("en", lang, query=q, limit=3):
                if p["tgt"] in seen or "?" in p["src"]:
                    continue
                seen.add(p["tgt"])
                lvl = LEVEL_BY_LEN(p["src"])
                others = [x for x in seen if x != p["tgt"]]
                if len(others) < 2:
                    continue
                opts = [p["src"]] + others[:2]
                items.append({"id": "pl%s" % len(items), "level": lvl,
                              "prompt": "«%s» — это…" % p["tgt"],
                              "options": opts, "answer": opts.index(p["src"]),
                              "source": "tatoeba"})
    if len(items) < 6:
        import content
        return content.PLACEMENT_ITEMS
    return sorted(items, key=lambda x: x["level"])[:12]
