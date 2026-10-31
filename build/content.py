"""Данные первого Python-билда: заглушка курса + колоды для placement.

Формат карточек соответствует shared/schema/deck_format.json (поля deck/card,
используемые ядром). Полная база уроков — отдельная задача чеклиста HANDOFF.
"""

# Заглушка курса из ТЗ: "Как переводится Hi? → Привет/Пока/Корова"
LESSON = {
    "id": "l001",
    "title": "Первые слова",
    "languages": ["en", "pt-BR", "zh"],
    "tasks": [
        # kind — тип задания (docs/SESSIONS.md), lang — язык карточки
        dict(kind="CHOICE_T2T", lang="pt-BR", prompt="Как переводится «Hi»?",
             options=["Привет", "Пока", "Корова"], answer=0),
        dict(kind="CHOICE_T2T", lang="zh", prompt="Как переводится «你好»?",
             options=["Привет", "Пока", "Корова"], answer=0),
        dict(kind="TYPE_EN2RU", lang="en", prompt="Переведи на русский: apple",
             expected=["яблоко"]),
        dict(kind="TYPE_EN2RU", lang="en", prompt="Переведи на русский: goodbye",
             expected=["пока", "до свидания"]),
        dict(kind="CHOICE_R2T", lang="pt-BR", prompt="Что значит «obrigado»?",
             options=["спасибо", "пожалуйста", "привет"], answer=0),
        dict(kind="TYPE_ZH2RU", lang="zh", prompt="Переведи: 牛",
             expected=["корова", "говядина"]),
        dict(kind="CHOICE_T2T", lang="zh", prompt="Как переводится «再见»?",
             options=["Привет", "Пока", "Корова"], answer=1),
        dict(kind="TYPE_PT2RU", lang="pt-BR", prompt="Переведи: tchau",
             expected=["пока"]),
        dict(kind="CHOICE_R2T", lang="en", prompt="Что значит «cow»?",
             options=["корова", "кошка", "курица"], answer=0),
        dict(kind="LISTEN_CHOOSE", lang="pt-BR", prompt="(аудио) tchau — это…",
             options=["пока", "привет", "спасибо"], answer=0),
    ],
}

# Мини-банк placement (level 1..6 → b = level - 0.5, docs/PLACEMENT.md)
PLACEMENT_ITEMS = [
    dict(id="p1", level=1, prompt="«Hello» — это…", options=["привет", "пока", "корова"], answer=0),
    dict(id="p2", level=1, prompt="«牛» — это…", options=["корова", "кошка", "дом"], answer=0),
    dict(id="p3", level=2, prompt="«obrigado» — это…", options=["спасибо", "извини", "да"], answer=0),
    dict(id="p4", level=2, prompt="Переведи: water", expected=["вода"]),
    dict(id="p5", level=3, prompt="Переведи: yesterday", expected=["вчера"]),
    dict(id="p6", level=3, prompt="«谢谢» — это…", options=["спасибо", "извините", "пожалуйста"], answer=0),
    dict(id="p7", level=4, prompt="Переведи: library", expected=["библиотека"]),
    dict(id="p8", level=4, prompt="«图书馆» — это…", options=["библиотека", "магазин", "больница"], answer=0),
    dict(id="p9", level=5, prompt="Переведи: although", expected=["хотя", "несмотря на то"]),
    dict(id="p10", level=5, prompt="«虽然» — это…", options=["хотя", "потому что", "если"], answer=0),
    dict(id="p11", level=6, prompt="Переведи: achievement", expected=["достижение"]),
    dict(id="p12", level=6, prompt="«achievement» по-португальски близко к…",
         options=["conquista", "aprendizado", "objetivo"], answer=0),
]
