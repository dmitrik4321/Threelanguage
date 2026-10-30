# Источники данных для уроков (DATA SOURCES)

Объём контента создаётся **конвейером из открытых датасетов**, вручную пишутся только правила грамматики и уточнения. Конвейер: `dump/raw → парсер → нормализация (wiktionary-подобный JSON) → генератор карточек по типам заданий → пакет колод (.threedeck)`. Всё офлайн-воспроизводимо, лицензии — свободные.

## 1. Лексика / переводы (ядро колод)

| Источник | Языки | Что даёт | Лицензия | Объём |
|---|---|---|---|---|
| **Wiktionary dumps** (`dump.wikimedia.org/…/latest-pages-meta-current.xml`) | все 100+ | переводы, части речи, примеры, МФА, темы | CC BY-SA 4.0 | основной источник |
| **OmegaWiki** (squall.et.fu-berlin.de) | EN + ~25 языков | машиночитаемые связи слово→перевод между парами | CC BY-SA | ~1M связей |
| **DBNL / BNC** (CLARIN) | NL/EN | частотные списки | CC BY | топ-N слов |
| ** Tatoeba** (downloads.tatoeba.org) | 400+ | готовые пары предложений для перевода и аудирования | CC BY 2.0 FR | ~12M предложений |
| **OPUS / OpenSubtitles** | многие | живые параллельные предложения (для примеров и диктантов) | разная, большинство free | гигантский |
| **Global Wordnets** (PWN, WnS,AWN…) | EN, RU, ES, DE, FA… | синонимы, гиперонимы — «выбери синоним» | собственная, open | — |
| **SWATI / NLTK wordlists** | EN | базовые частотники | public domain | — |

Частотные списки (чтобы первые уроки были про самое нужное):
- EN: **COCA top-5000** (или freer: `english-frequency-project`), SUBTLEX-UK.
- RU: **Russian National Corpus** частоты; открытая копия — `ruscorpora_freq_lists` на GitHub.
- DE/ES/FR: SUBTLEX-DE / Lexique-20 (FR, CC-BY) / CDEL (ES).
- JA: **JLPT-списки** (jisho.org API, N5..N1) + NHK-частотник.
- ZH: HSK 1–3 (официальные слова, есть свободные реплики) + SUBTLEX-CH.
- KO: TOPIK I/II vocabulary lists (NIKL dataset, CC-BY).

## 2. CJK-специфика (масштабируемость по ТЗ)

| Данные | Источник | Лицензия |
|---|---|---|
| JA словарь | **JMdict/JEDICT** (Jisho mirror, Monash) | CC BY-SA |
| JA чтение имён кундoku | KanjiVG (штрихи для SCRIPT_WRITE!) + unihan | CC BY-SA |
| ZH словарь | **CC-CEDICT** | CC BY-SA 4.0 |
| ZH ханьчжи: пиньинь, тон, штрихи | Unihan database (unicode.org), KanjiVG для CJK统一 | free |
| KO слова | **KAIST dictionary** (+ SenseIcon) | BSD |
| KO хангыль-разбор (batchim) | вычисляется алгоритмически, данных нет | — |
| Порядок черт для рисования | **KanjiVG SVG** (6800 знаков, с анимацией) | CC BY-SA |

Это закрывает «письменное» задание для иероглифов: карточка содержит SVG-скелет знака, пользователь водит стилусом/пальцем, сравнение по точкам траектории (DTW) — см. SESSIONS.md, тип SCRIPT_WRITE.

## 3. Аудирование (звук)

Приоритеты:
1. **Готовые озвучки**: Wiktionary `Audio:` файлы (OGG/MP3, носители; CC-лицензии), Forvo (API платный — не берём), LibriVox-фрагменты (public domain, длинные — режем VAD'ом).
2. **TTS offline-костяк** (уже в бэкенде как прокси):
   - EU-языки: **espeak-ng** (free, robotic но годится для MVP) → позже **Piper** (нейро, реально, ~60 языков, CPU-friendly, MIT).
   - JA/ZH/KO: Piper пока слаб → **Coqui TTS модели XTTS-v2** (MPL, требует GPU на сервере) или системные голоса Android (TextToSpeech API) как fallback на клиенте.
3. **Диктанты из живой речи**: OpenSubtitles + Whisper-large (локально, один раз при сборке датасета) → готовый таймкодовый текст. Дорого, но это лучший источник «настоящего» аудирования; делаем батчами на сервере сборки.

ФонематическиеMinimal pairs: генерятся из MFA-dictionary (Speech Marketplace, BSD) — «услышь различие ship/sheep».

## 4. Грамматика и «правила урока»

- **Universal Dependencies treebanks** (universaldependencies.org, CC-BY-SA) — морфология/синтаксис всех языков: автоматически строим задания «просклоняй/проспрягай», выбор падежа.
- Таблицы спряжений: **Verbix**-парсинг (осторожно с лицензией) или Vikidia/Wiktionary conjugation tables (CC BY-SA).
- Короткие текстовые объяснения правил — LLM-генерация поверх UD-фич + ручная вычитка волонтёрами (единственный этап где нужен человек; объём мал: ~200 правил на язык, а не 20 000 карточек).

## 5. Уровень-тест (placement)

Банки вопросов уже существуют:
- CEFR-маркированные списки: **English Profile** (GPRL, CC BY-NC — уточнить), EPELEX.
- JLPT N5–N1 past papers (BSD-ish free distribution), HSK Official samples, TOPIK sample tests.
- Генератор адаптивных вопросов из нашей же колоды по уровням частотности (fallback без внешних лицензий).

## 6. Импорт существующего (мгновенный контент)

- **Anki shared decks** (ankiweb.net/shared/decks) — формат `.apkg` = SQLite, импортёр уже запланирован (backend `decks.py`). Лицензии разные — фильтруем по CC/公有, метаданные сохраняем.
- **Core Vocabulary Project** (github.com/core-vocab) — готовые 1000-словных списков на десятки языков, CC BY-SA. Идеальный стартовый пакет.

## 7. Пайплайн сборки (что пишем первыми)

```
tools_data/
  fetch_wiktionary.py   # xml dump -> json {word, lang, senses[], ipa, audio[]}
  fetch_freq.py         # частотники -> rank per language
  pair_translations.py  # OmegaWiki+Wiktionary cross -> парные карточки A<->B
  tatoeba_pairs.py      # sentence pairs для TYPE/LISTEN
  build_deck.py         # freq-rank + translations -> .threedeck (schema deck_format.json)
  tts_batch.py          # espeak-ng/Piper -> mp3 cache, привязка к карточкам
  kanji_svg.py          # KanjiVG -> траектории черт для SCRIPT_WRITE
```
Стартовый прогон: 3 языка (RU/EN/DE) × топ-2000 слов + 500 предложений Tatoeba + placement-наборы = полный MVP-контент без ручной работы.

## Открытые вопросы
1. Piper vs espeak-ng для MVP-аудирования? (Piper лучше качеством, тяжелее в сборке ~2 ГБ моделей)
2. Whisper-диктанты включаем в v0.2 или откладываем?
3. Импорт Anki-колод: разрешаем несвободные лицензии внутри приложения (пользователь сам ответственен) или фильтр только CC?
