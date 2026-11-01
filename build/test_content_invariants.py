"""Офлайн-тесты инвариантов Квена Старшего (сборка уроков/placement).

Инварианты (см. диагноз по скриншоту «She wants water?» с тремя EN-вариантами):
  1) CHOICE: язык(варианты) != язык(prompt-фразы источника) — варианты обязаны
     быть переводами, а не цитатами источника;
  2) ровно один вариант == правильный перевод prompt (по связи Tatoeba
     translations), т.е. answer указывает на непустой option и он != source;
  3) без дублей вариантов;
  4) TYPE: expected непуст и != prompt-фраза.
Нарушение любого -> карточка не должна попадать в выдачу validate_tasks.

Тесты ОФЛАЙН: живые API не нужны, подаём синтетические карточки прямо в
validate_tasks/_mix_kinds — так баг воспроизводится чище, чем через браузер
(методика из вердикта).
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
import data_sources as ds

FAILS = []

def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        FAILS.append(name)

# --- 1. Регресс бага: EN-prompt + три EN-варианта должен ОТБАСЫВАТЬСЯ --------
bad_choice = {"kind": "CHOICE_T2T", "lang": "pt-BR", "level": 1,
              "prompt": "Как переводится «She wants water»?",
              "options": ["She needs water.", "Water, please!", "Is anybody here?"],
              "answer": 0}
check("regress: EN-options for EN-source rejected",
      ds.validate_tasks([bad_choice]) == [])

# --- 2. Валидная пара: EN-source + PT-переводы ------------------------------
good_choice = {"kind": "CHOICE_T2T", "lang": "pt-BR", "level": 1,
               "prompt": "Как переводится «She wants water»?",
               "options": ["Ela quer água.", "Ele tem fome.", "Nós vamos embora."],
               "answer": 0, "source_lang": "en", "target_lang": "pt-BR"}
check("valid: EN-source + PT-target passes",
      len(ds.validate_tasks([good_choice])) == 1)

# --- 3. Ответ == цитата источника -> rejection --------------------------------
quoted = dict(good_choice); quoted["options"] = ["She wants water.", "Ele tem fome.", "Nós vamos embora."]
check("reject: answer is quote of source phrase",
      ds.validate_tasks([quoted]) == [])

# --- 4. Дубли вариантов -> rejection ------------------------------------------
dup = dict(good_choice); dup["options"] = ["Ela quer água.", "Ela quer água.", "Nós vamos embora."]
check("reject: duplicate options", ds.validate_tasks([dup]) == [])

# --- 5. Пустой правильный вариант -> rejection --------------------------------
empty = dict(good_choice); empty["options"][0] = ""
check("reject: empty correct option", ds.validate_tasks([empty]) == [])

# --- 6. Смешанные скрипты вариантов -> rejection -------------------------------
mixed = dict(good_choice); mixed["options"] = ["Ela quer água.", "水です。", "Nós vamos embora."]
check("reject: mixed scripts in options", ds.validate_tasks([mixed]) == [])

# --- 7. _mix_kinds: правильный перевод присутствует, дистракторы — tgt-язык ---
raw = [
    {"kind": "CHOICE_T2T", "lang": "pt-BR", "level": 1, "source": "tatoeba",
     "license": "CC-BY", "prompt": "«I am hungry» (eu estou com fome)",
     "_answer_en": "I am hungry", "_tgt_text": "Eu estou com fome"},
    {"kind": "CHOICE_T2T", "lang": "pt-BR", "level": 1, "source": "tatoeba",
     "license": "CC-BY", "prompt": "«She wants water» (ela quer água)",
     "_answer_en": "She wants water", "_tgt_text": "Ela quer água"},
    {"kind": "CHOICE_T2T", "lang": "pt-BR", "level": 1, "source": "tatoeba",
     "license": "CC-BY", "prompt": "«Good morning» (bom dia)",
     "_answer_en": "Good morning", "_tgt_text": "Bom dia"},
]
out = ds._mix_kinds(raw)
choices = [t for t in out if t.get("kind","").startswith("CHOICE")]
ok_mix = bool(choices) and all(
    len(t["options"]) == 3
    and t["options"][t["answer"]] in {"Eu estou com fome", "Ela quer água", "Bom dia"}
    and all(_c != "" for _c in t["options"])
    for t in choices)
check("_mix_kinds: correct translation present, no EN quotes", ok_mix)
# позиции правильного варианта варьируются (не всегда 0) на длинной партии
many_raw = []
for n in range(6):
    many_raw.append({"kind":"CHOICE_T2T","lang":"pt-BR","level":1,"source":"tatoeba",
                     "license":"CC-BY","prompt":"«s%d» (t%d)" % (n,n),
                     "_answer_en":"sentence number %d" % n,"_tgt_text":"traducao %d" % n})
many = [t for t in ds._mix_kinds(many_raw) if t.get("kind","").startswith("CHOICE")]
check("_mix_kinds: answer position not always 0",
      len({t["answer"] for t in many}) > 1 if many else False)

# --- 8. _mix_kinds деградирует в typing, если дистракторов < 2 ----------------
one = [{"kind": "CHOICE_T2T", "lang": "zh", "level": 1, "source": "tatoeba",
        "license": "CC-BY", "prompt": "«water» (shuǐ)",
        "_answer_en": "water", "_tgt_text": "水"}]
res = ds._mix_kinds(one)
check("_mix_kinds: single-card zh batch yields no broken CHOICE",
      all(not t.get("kind","").startswith("CHOICE") or len(t.get("options",[]))==3
          and t["options"][t["answer"]] != t["options"][0] == "" for t in res))

# --- 9. placement-инвариант: items из build_lesson-подобной сборки ------------
pl_bad = {"id":"p1","level":1,"kind":"CHOICE_T2T","lang":"pt-BR",
          "prompt":"Как переводится «Hello»? (выбери вариант)",
          "options":["Hello there.","Hello again.","Say hello."],"answer":0,
          "source_lang":"en","target_lang":"en"}   # как при баге: язык=язык
check("placement validator rejects same-language options",
      ds.validate_tasks([pl_bad]) == [])

# --- 10. TYPE инвариант ---------------------------------------------------------
tp_good = {"kind":"TYPE_EN2RU","lang":"en","level":1,"prompt":"Переведи на «en»: apple",
           "expected":["apple"]}
tp_bad  = {"kind":"TYPE_EN2RU","lang":"en","level":1,"prompt":"Переведи на «en»: apple",
           "expected":["apple"]}
# expected == source-фраза промпта ("apple") -> вопрос бессмысленный, reject
check("TYPE: expected equal to prompt phrase rejected",
      ds.validate_tasks([tp_bad]) == [])

print()
if FAILS:
    print("INVARIANTS FAILED: %d" % len(FAILS)); sys.exit(1)
print("ALL INVARIANTS PASS (%d checks)" % 10); sys.exit(0)
