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
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

CACHE_PATH = Path.home() / ".threelanguage" / "content_cache.json"
CACHE_TTL = 7 * 86400
UA = {"User-Agent": "ThreelanguageDesktop/0.1 (learning app; contact: local)"}

# Rate-limit защита чужого API (см. docstring _cached_get): не более 3
# одновременных запросов и не чаще одного запроса каждые 6 секунд (~10/мин).
_NET_SEMAPHORE = threading.Semaphore(3)
_MIN_INTERVAL = 6.0
_last_request_ts = [0.0]

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
    """HTTP GET с disk-кэшем и деликатным отношением к чужому API.

    Возвращает None при любой сетевой ошибке (или отдаёт старый кэш).
    Rate-limit: у Tatoeba лимит ~10 запросов/мин на IP (429 Too Many Requests,
    проверено живым прогоном: 15 параллельных запросов пулом = почти всё 429).
    Поэтому: (1) глобальный semaphore — не более 3 одновременных запросов;
    (2) минимальный интервал между запросами 6с (~10/мин);
    (3) на 429 повтор через 20с один раз, затем фолбэк в кэш/None.
    Уроки строятся в фоне (worker-поток GUI / серверный lesson-cache), UI не ждёт.
    """
    cache = _cache_load()
    ent = cache.get(url)
    if ent and time.time() - ent["ts"] < CACHE_TTL:
        return ent["data"]
    for attempt in range(2):
        with _NET_SEMAPHORE:
            wait = _MIN_INTERVAL - (time.time() - _last_request_ts[0])
            if wait > 0:
                time.sleep(wait)
            _last_request_ts[0] = time.time()
            try:
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read().decode("utf-8"))
                cache[url] = {"ts": int(time.time()), "data": data}
                _cache_save(cache)
                return data
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt == 0:
                    time.sleep(20)      # уважаем чужой лимит, повторяем один раз
                    continue
                return ent["data"] if ent else None
            except Exception:
                return ent["data"] if ent else None   # сеть легла — старый кэш
    return ent["data"] if ent else None


# --------------------------------------------------------------- Tatoeba ----
def tatoeba_pairs(source: str, target: str, query: str = "", limit: int = 10) -> list[dict]:
    """Фразы source→target c переводами. Пустой список если API недоступен.

    Каждая пара: {src, tgt, src_lang, tgt_lang, transcriptions, license}.
    """
    f, t = TATOEBA_LANG.get(source, source), TATOEBA_LANG.get(target, target)
    # sort=random вместо relevance: relevance-выдача по коротким запросам почти
    # всегда одна и та же («вода» => одни и те же 10 фраз), из-за чего урок на
    # 5 CORE_WORDS давал дубликаты и карточки вылетали по дедупу. random даёт
    # разнообразные пары; кэш TTL фиксирует выдачу в рамках часа.
    url = ("https://tatoeba.org/en/api_v0/search?from=%s&to=%s&sort=random&per_dir_limit=3"
           % (f, t) + ("&query=%s" % urllib.parse.quote(query) if query else ""))
    data = _cached_get(url)
    out = []
    for res in (data or {}).get("results", [])[:limit] if isinstance(data, dict) else []:
        groups = res.get("translations", [])
        # ФОРМАТ ОТВЕТА Tatoeba v0 (проверено живым запросом): translations —
        # список ГРУПП по языкам; группа — список объектов-переводов. Разворачиваем
        # ВСЕ элементы всех групп (старый flat=g[0] оставлял за бортом 2..N перевод
        # каждой группы). Дополнительно снят фильтр isDirect: поле в выдаче v0
        # отсутствует (живой снимок: 0 объектов с isDirect), а x.get("isDirect", True)
        # на отсутствующем ключе истинен — но при изменении формата API он молча
        # терял бы пары; надёжный критерий — язык перевода.
        cand = []
        for g in groups:
            if isinstance(g, dict):
                cand.append(g)
            elif isinstance(g, list):
                cand.extend(x for x in g if isinstance(x, dict))
        tr = next((x for x in cand if x.get("lang") == t), None)
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


# --------------------------------------------------------------------------
# Кэш собранных УРОКОВ (в отличие от HTTP-кэша выше — это кэширует уже
# готовую колоду). Причина: /api/content/lesson вызывал build_lesson на
# каждый запрос; при холодном disk-кэше это ~15 последовательных запросов
# к Tatoeba (timeout до 6с каждый) => веб-клиент «висел» десятки секунд.
# TTL короткий (1 час): контент живой, но за час ничего не изменится, а
# приложение офлайн-first и синхронизируется через сервер.
LESSON_CACHE_PATH = Path.home() / ".threelanguage" / "lesson_cache.json"
LESSON_TTL = 3600


def _lesson_cache_load() -> dict:
    try:
        return json.loads(LESSON_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _lesson_cache_save(cache: dict) -> None:
    try:
        LESSON_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        LESSON_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False),
                                     encoding="utf-8")
    except Exception:
        pass  # best effort


def cached_build_lesson(langs: list[str]) -> dict:
    """build_lesson с кэшем собранной колоды (для UI/API-вызовов)."""
    key = ",".join(sorted(langs))
    cache = _lesson_cache_load()
    ent = cache.get(key)
    if ent and time.time() - ent["ts"] < LESSON_TTL:
        return ent["data"]
    lesson = build_lesson(langs)
    # офлайн-фолбэк НЕ кешируем надолго: вернёмся за живым контентом быстрее
    if lesson.get("id") == "live":
        cache[key] = {"ts": int(time.time()), "data": lesson}
        _lesson_cache_save(cache)
    return lesson


def build_lesson(langs: list[str]) -> dict:
    """Собирает урок из живых источников. При недоступности API возвращает
    offline-колоду data/vocab.json (или встроенную заглушку content.LESSON).

    Ядро (shared/algorithm/reference.py) не меняется — карточки приходят в
    тех же форматах kind, что принимали заглушки.

    Параллелизм: запросы к API идут в пуле потоков (сеть — latency-bound);
    для десктопного урока (~15 фраз) это разница между десятками секунд и
    ~2 секундами. Для UI-вызовов используй cached_build_lesson()."""
    from concurrent.futures import ThreadPoolExecutor

    jobs = []  # (lang, word) — по 5 слов максимум на язык
    for lang in langs:
        for w in CORE_WORDS[:5]:
            jobs.append((lang, w))

    with ThreadPoolExecutor(max_workers=8) as pool:
        # (lang, word) -> парные запросы к Tatoeba параллельно
        results = list(pool.map(
            lambda j: (j[0], tatoeba_pairs("en", j[0], query=j[1], limit=3)), jobs))

    tasks, seen = [], set()
    per_lang_count = {lang: 0 for lang in langs}
    for lang, pairs in results:
        if per_lang_count[lang] >= 5:
            continue
        for p in pairs:
            if per_lang_count[lang] >= 5:
                break
            key = _clean(p["tgt"])
            if key in seen or len(p["src"].split()) > 8 or len(p["src"]) < 4:
                continue
            seen.add(key)
            tasks.append(_task_from_pair(lang, p))
            per_lang_count[lang] += 1
    if not tasks:
        return _offline_fallback(langs)
    mixed = _mix_kinds(tasks)
    if not mixed:                      # весь живой контент не прошёл инварианты
        return _offline_fallback(langs)
    return {"id": "live", "title": "Живой урок · Tatoeba (CC BY)",
            "languages": langs, "tasks": mixed}


def _task_from_pair(lang: str, p: dict) -> dict:
    """Пара Tatoeba -> карточка. Для zh показываем транслитерацию (pinyin),
    только если она реально отличается от текста (иначе это дубль иероглифов)."""
    base = {"kind": "CHOICE_T2T", "lang": lang, "level": LEVEL_BY_LEN(p["src"]),
            "source": "tatoeba", "license": p["license"]}
    tr = (p.get("transcriptions") or [""])[0]
    hint = ("  (%s)" % tr) if tr and _clean(tr) != _clean(p["tgt"]) else ""
    t = dict(base, prompt="Как переводится «%s»%s?" % (p["tgt"], hint))
    t["_answer_en"] = p["src"]          # служебное: EN-исходник пары
    t["_tgt_text"] = p["tgt"]           # служебное: ground-truth перевод (tgt-язык)
    return t


def _clean(s: str) -> str:
    """Нормализация фразы для дистракторов: без кавычек, знаков конца, пробелов-подряд."""
    import re as _re
    s = _re.sub(r"[“”\"«»]", "", s)
    return _re.sub(r"\s+", " ", s.strip().rstrip(".!?。！？…")).lower()


def _is_cjk(s: str) -> bool:
    return any(0x2E80 <= ord(c) <= 0x9FFF for c in s)


def _mix_kinds(tasks: list[dict]) -> list[dict]:
    """Превращает сырые CHOICE-заготовки в разные типы заданий ядра.

    Исправление бага «неотвечаемого вопроса» (диагноз Квена Старшего): ранее
    дистракторы брались из `x.get('_answer_en')` у СОСЕДНИХ карточек, т.е. из
    того же языка, что и сам ответ (EN). Получался вопрос «Как переводится
    She wants water?» с тремя английскими вариантами — правильного перевода
    физически не было в выборке, пользователь не мог ответить, а экран держал
    polling-петлю в шторме повторных запросов контента.

    Теперь инвариант сборки (проверяется и регресс-тестом smoke_test.py):
      * prompt показывает фразу ИСТОЧНИКА (EN), варианты — переводы на язык
        изучения (tgt); т.е. язык options != язык prompt-фразы;
      * ровно один option == перевод prompt по связи Tatoeba translations,
        дистракторы — переводы соседних EN-фраз (тоже tgt-язык).
    Если чистых дистракторов меньше двух — карточка деградирует до typing
    (TYPE_*2RU), а не до битого выбора."""
    finals = []
    for i, t in enumerate(tasks):
        ans = t.pop("_answer_en")          # EN-исходник этой карточки
        tgt = t.pop("_tgt_text", "")       # её перевод (tgt-язык)
        na = _clean(ans)
        # Prompt — фраза источника (EN), правильный вариант — её перевод (tgt).
        # P.S. pinyin-подсказка (если была в скобках) переносится к переводу-варианту
        # не может: он и есть текст перевода; подсказку оставляем в конце prompt.
        m = __import__("re").search(r"\((.*?)\)", t["prompt"])
        paren = " (%s)" % m.group(1) if m else ""
        # pinyin-подсказку НЕ кладём в prompt: валидатор сравнивает скрипт
        # вариантов со скриптом prompt, и CJK-иероглифы подсказки забивали бы
        # все zh-CHOICE-карточки как «варианты на языке prompt». Подсказка —
        # отдельное поле UI.
        t["hint"] = m.group(1) if m else ""
        t["prompt"] = "Как переводится «%s»?" % ans
        right = tgt or OFFLINE_GLOSS.get(na, "")
        # Дистракторы — переводы соседних карточек ТОГО ЖЕ языка изучения:
        # иначе вопрос невалиден (например, португальский prompt с китайским
        # вариантом пользователь не может судить корректно).
        want_cjk = (t["lang"] == "zh")
        cand = [x.get("_tgt_text") or OFFLINE_GLOSS.get(_clean(x.get("_answer_en") or ""), "")
                for x in tasks if x is not t and _is_cjk(x.get("_tgt_text") or "") == want_cjk]
        distr, used = [], {right}
        for o in cand:
            if o and len(o) <= 40 and _clean(o) not in used:
                distr.append(o); used.add(_clean(o))
            if len(distr) == 2:
                break
        t["source_lang"] = "en"; t["target_lang"] = t["lang"]
        if right and len(distr) >= 2:
            opts = [right] + distr
            # Детермин. перемешивание ДО определения индекса: иначе правильный
            # вариант всегда сидел бы на позиции 0 (угадываемость) либо индекс
            # терялся при поиске в перемешанном списке (баг v15: answer=0 всегда).
            rot = i % 3
            opts = [opts[(j + rot) % 3] for j in range(3)]
            correct = next((j for j, o in enumerate(opts) if _clean(o) == _clean(right)), 0)
            t["options"], t["answer"] = opts, correct
            finals.append(t)
        elif lang_of_ru(t["lang"]) == "ru":
            # typing-карточка: показать EN-фразу, требовать ПЕРЕВОД на язык
            # изучения (ground truth = tgt; OFFLINE_GLOSS — фолбэк для en-пар).
            # Инвариант: expected != prompt-фраза, судья ядра сравнивает ввод
            # пользователя с expected через partial_credit(NFKC+Levenshtein).
            ru = OFFLINE_GLOSS.get(na, "")
            exp = [x for x in ([ru, right] if ru else [right]) if x and _clean(x) != na]
            if not exp:
                continue                                 # судить нечем — выбросить
            finals.append(dict(kind="TYPE_EN2RU", lang=t["lang"], level=t["level"],
                               prompt="Переведи на «%s»: %s" % (t["lang"], ans),
                               expected=exp, source_lang="en", target_lang=t["lang"],
                               source="tatoeba", license=t["license"]))
        else:
            # нет ни двух дистракторов, ни ru-опоры — карточку выбрасываем,
            # неотвечаемый вопрос показывать нельзя
            continue
    for f in finals:                      # чистим служебные поля перед выдачей UI
        f.pop("_answer_en", None); f.pop("_tgt_text", None)
    validated = validate_tasks(finals)
    # Пусто после валидации -> НЕ отдаём пустой/битый урок: build_lesson
    # увидит пустой список и вернёт офлайн-колоду (см. ниже по стеку).
    return validated


def _strip_prompt(prompt: str) -> str:
    """Вычленить саму фразу из шаблона промпта: «Как переводится «X»?/»?» /
    «Переведи на ...: X» -> X. Нужен для проверки «вариант == prompt-фраза».
    Неизвестный формат возвращаем как есть."""
    import re as _re
    m = _re.search(r"[«\"'](.*?)[»\"']", prompt)
    if m:
        return m.group(1)
    m = _re.search(r":\s*(.+)$", prompt)
    if m:
        return m.group(1)
    return prompt


def _latin(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalpha())


def _script_of(s: str) -> str:
    """Доминирующий скрипт строки (latin / cyrillic / cjk / mixed).
    Считаем буквы каждого письма; победитель — у кого больше. Пустая/безбуквенная
    строка -> "". Это нужно для инварианта «язык вариантов != язык prompt»:
    сравнение по ДОМИНАНТЕ устойчиво к латинским вкраплениям в кириллических
    фразах («Hello! Minghui.» на китайском уроке = доминирует CJK)."""
    lat = sum(1 for c in s if ("a" <= c <= "z") or ("A" <= c <= "Z"))
    cyr = sum(1 for c in s if "\u0400" <= c <= "\u04FF")
    cjk = sum(1 for c in s if _is_cjk(c))
    best = max(lat, cyr, cjk)
    if best == 0:
        return ""
    # доминанта должна быть явно сильнее второго кандидата (иначе смешанный текст
    # не судим по скрипту — считаем mixed и правило «!=» к нему не применяем жёстко)
    winners = [n for n in (lat, cyr, cjk) if n == best]
    if len(winners) > 1:
        return "mixed"
    return "latin" if lat == best else ("cyrillic" if cyr == best else "cjk")


def _extract_prompt_phrase(prompt: str) -> tuple[str, str]:
    """Вернуть (фраза-источник, скрипт_шаблона).

    Живые уроки строятся на русском UI. Если фраза-источник и её «переводы»
    написаны одним письмом (латиница↔латиница), пользователь не может судить,
    какой вариант перевод — правильный выпадает из выборки (эталонный баг
    Квена). Различимые пары скриптов (EN→RU/CJK, CJK→RU/Latin) легальны."""
    import re as _re
    # Сначала проверяем шаблон ввода: «Переведи ...: X» — там переводимая фраза
    # стоит ПОСЛЕ двоеточия и кавычки в обрамлении («на «en»») не относятся к ней.
    mm = _re.search(r":\s*(.+)$", prompt)
    if mm and ("Переведи" in prompt or "переведи" in prompt):
        phrase = mm.group(1)
    else:
        m = _re.search(r"[«\"'](.*?)[»\"']", prompt)
        phrase = m.group(1) if m else _re.sub(r"^\(аудио\)\s*", "", prompt)
    frame = prompt.replace(phrase, "", 1)
    return phrase, _script_of(frame)


# Легальные комбинации «скрипт источника -> скрипт вариантов».
# Латиница к латинице НЕ легальна как класс (EN→pt/en варианты неотличимы для
# русскоязычного пользователя), НО живой pt-BR/zh урок строит prompt на языке
# ИСТОЧНИКА (EN), а варианты — переводы на язык изучения; тогда скрипты совпадают
# только для пар en/en (дубликат) и это отсекается правилом «ответ != цитата +
# дистракторы одного языка с ответом». Для колоды LESSON источник EN, варианты RU.
_ALLOWED_TARGETS = {
    "latin": {"cyrillic", "cjk", "latin"},      # latin->latin разрешён ниже точечно
    "cyrillic": {"latin", "cjk"},
    "cjk": {"latin", "cyrillic"},
    "mixed": {"latin", "cyrillic", "cjk", "mixed"},
}


def validate_tasks(tasks: list[dict]) -> list[dict]:
    """Фильтр по инвариантам Квена Старшего. Возвращает только валидные задания.

    CHOICE_*: скрипт вариантов обязан быть переводимой парой к скрипту
    фразы-источника; без дублей; answer указывает на непустой option, не
    совпадающий с самой prompt-фразой. LISTEN_* (аудио): перевода в промпте нет,
    проверяем единый скрипт вариантов и отсутствие дублей. TYPE_*: expected
    непустой и отличается от prompt-фразы. Нарушение любого пункта -> НЕ показывать."""
    ok = []
    for t in tasks:
        kind = t.get("kind", "")
        if kind.startswith("CHOICE") or kind.startswith("LISTEN"):
            opts = t.get("options") or []
            ans = t.get("answer", -1)
            if len(opts) != 3 or not (0 <= ans < 3):
                continue
            if len({_clean(o) for o in opts}) != 3:      # дубль варианта = нечестный выбор
                continue
            if not _clean(opts[ans]):                    # правильный вариант пуст
                continue
            scripts = {_script_of(o) for o in opts} - {""}
            if kind.startswith("LISTEN"):
                if len(scripts) != 1:                    # аудио: все варианты одного письма
                    continue
                ok.append(t); continue
            phrase, _frame = _extract_prompt_phrase(t.get("prompt", ""))
            sp = _script_of(phrase)
            if not sp or len(scripts) != 1:              # смешанный набор вариантов
                continue
            vo = next(iter(scripts))
            allowed = _ALLOWED_TARGETS.get(sp, set())
            if vo not in allowed:
                continue                                 # переводимой пары нет
            # latin->latin допустимо ТОЛЬКО если ни один вариант не равен source-фразе
            # (т.е. правильный перевод реально присутствует среди похожих строк).
            if sp == "latin" and vo == "latin":
                # Инвариант Квена Старшего строго: перевод обязан быть НА ДРУГОМ
                # языке, т.е. для русскоязычного пользователя различимом. EN-фраза
                # с латиничными вариантами легальна ТОЛЬКО когда вариант — не
                # англоязычная фраза: проверяем отсутствие общих знаменателей —
                # эвристика: совпадение первых слов с источником недопустимо, и
                # любой вариант, состоящий из «английских» слов-дубликатов
                # источника, отсекается. Полный же фильтр — поле t["target_lang"]:
                # живой конвейер кладёт его всегда; если target_lang == язык
                # источника (en/en) — карточка невалидна по построению.
                tl = (t.get("target_lang") or "").lower()
                sl = (t.get("source_lang") or "").lower()
                if tl and sl and tl.split("-")[0] == sl.split("-")[0]:
                    continue                             # en->en: перевода не существует
                if any(_clean(o) == _clean(phrase) for o in opts):
                    continue                             # цитата источника в вариантах
                # Языки НЕ объявлены конвейером -> отличить перевод от цитаты
                # нельзя, а «неотличимо глазами» = ровно эталонный баг Квена
                # (EN-prompt + 3 EN-варианта). Консервативно запрещаем класс.
                if not (tl and sl):
                    continue
            if _clean(opts[ans]) == _clean(phrase):      # ответ == цитата источника
                continue
            ok.append(t)
        elif kind.startswith("TYPE"):
            exp = t.get("expected") or []
            phrase, _ = _extract_prompt_phrase(t.get("prompt", ""))
            phrase = _clean(phrase)
            if exp and any(_clean(e) and _clean(e) != phrase for e in exp):
                ok.append(t)
    return ok


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


PLACEMENT_CACHE_PATH = Path.home() / ".threelanguage" / "placement_cache.json"


def _pcache_load() -> dict:
    try:
        return json.loads(PLACEMENT_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pcache_save(cache: dict) -> None:
    try:
        PLACEMENT_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PLACEMENT_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False),
                                       encoding="utf-8")
    except Exception:
        pass


def cached_placement_items(langs: list[str]) -> list[dict]:
    """placement_items с кэшем (TTL как у урока). Для UI/API-вызовов."""
    key = ",".join(sorted(langs))
    cache = _pcache_load()
    ent = cache.get(key)
    if ent and time.time() - ent["ts"] < LESSON_TTL:
        return ent["data"]
    items = placement_items(langs)
    # фолбэк-заглушку не кешируем: при появлении сети отдадим живой банк
    if items and items[0].get("source") == "tatoeba":
        cache[key] = {"ts": int(time.time()), "data": items}
        _pcache_save(cache)
    return items


def placement_items(langs: list[str]) -> list[dict]:
    """Placement-банк: фразы разного уровня из Tatoeba (level 1..6 по длине).
    Если API недоступен — content.PLACEMENT_ITEMS (заглушка).

    Как и build_lesson, запросы к API идут параллельно в пуле потоков;
    порядок обхода детерминирован (по индексам), чтобы банк был
    воспроизводим между запусками."""
    from concurrent.futures import ThreadPoolExecutor

    lang0 = (langs[:1] or ["en"])[0]
    lang = lang0
    with ThreadPoolExecutor(max_workers=8) as pool:
        fetched = list(pool.map(
            lambda w: tatoeba_pairs("en", lang, query=w, limit=3), CORE_WORDS))

    # Инвариант placement (то же семейство багов, что «неотвечаемый вопрос»
    # урока): prompt — EN-фраза источника, варианты — переводы на язык изучения
    # (поле tgt пар Tatoeba), ровно один вариант == правильный перевод prompt.
    # Прежняя реализация клала в options сам p["src"] (EN) рядом с tgt-дистрак-
    # торами -> смешение языков и неверный correct-индекс (answer был захардкожен 0).
    pairs_all = [p for pairs in fetched for p in pairs if "?" not in p["src"]]
    pairs_all.sort(key=lambda x: len(x["src"]))   # детерминированно: короткие->длинные
    # Второй пул: переводы тех же EN-фраз на ДРУГОЙ язык урока (pt/zh). Они и
    # есть дистракторы — корректные переводы соседних фраз, как в _mix_kinds.
    other_lang = next((l for l in langs if l != lang0), None)
    distr_pool = []
    if other_lang:
        with ThreadPoolExecutor(max_workers=8) as pool:
            od = list(pool.map(lambda w: tatoeba_pairs("en", other_lang, query=w, limit=2),
                               CORE_WORDS[:6]))
        seen_d = set()
        for pairs in od:
            for q in pairs:
                if "?" in q["src"] or q["tgt"] in seen_d:
                    continue
                seen_d.add(q["tgt"])
                distr_pool.append(q["tgt"])
    items, used_src, used_tgt = [], set(), set()
    pool_seen = []                                  # [(src,tgt)] для дистракторов
    for p in pairs_all:
        src, tgt = p["src"], p["tgt"]
        if src in used_src or tgt in used_tgt:
            continue
        # вариант-перевод должен ОТЛИЧАться от языка prompt (инвариант Квена):
        # берём tgt-дистракторы из того же языка изучения + подстраховка из
        # distr_pool (другой язык урока) если своих пока мало.
        cand_d = [t for s, t in pool_seen if t != tgt]
        cand_d += [t for t in distr_pool if t != tgt and t not in cand_d]
        distr = cand_d[:2]
        if len(distr) < 2:
            pool_seen.append((src, tgt))
            continue
        opts = [tgt] + distr
        k = (len(items) + 1) % 3                    # детермин. перемешивание
        opts = [opts[(i + k) % 3] for i in range(3)]
        correct = next(j for j, o in enumerate(opts) if o == tgt)
        lvl = LEVEL_BY_LEN(src)
        items.append({"id": "pl%s" % len(items), "level": lvl,
                      "prompt": "Как переводится «%s»? (выбери вариант)" % src,
                      "options": opts, "answer": correct,
                      "source": "tatoeba"})
        used_src.add(src); used_tgt.add(tgt)
        pool_seen.append((src, tgt))
    # Порог 3 (было 6): раньше при бедном живом банке возвращалась СТАРАЯ
    # заглушка content.PLACEMENT_ITEMS — именно она давала вопросы типа
    # «Hello → привет/пока/корова». Живые 3 валидных предмета честнее мёртвой
    # заглушки; совсем пусто — тогда уже офлайн.
    if len(items) < 3:
        import content
        return content.PLACEMENT_ITEMS
    return sorted(items, key=lambda x: x["level"])[:12]
