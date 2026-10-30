"""Прогон golden-фикстур эталоном. CI вызывает этот скрипт + тесты Kotlin/C++."""
import json, sys, os
sys.path.insert(0, os.path.dirname(__file__))
import reference as r
from dataclasses import asdict

TOL = 1e-6
fails = 0

def check(name, got, exp):
    global fails
    ok = True
    if isinstance(exp, dict):
        for k, v in exp.items():
            g = got.get(k) if isinstance(got, dict) else getattr(got, k, None)
            if isinstance(v, float) and isinstance(g, (int, float)):
                if abs(v - g) > TOL: ok = False
            elif g != v: ok = False
    elif isinstance(exp, (int, float)) and isinstance(got, (int, float)):
        ok = abs(exp - got) <= TOL
    else:
        ok = got == exp
    print(("PASS" if ok else "FAIL"), name)
    if not ok:
        print("   expected:", exp, "\n   got     :", got)
        fails += 1

fx = os.path.join(os.path.dirname(__file__), "fixtures")
data = json.load(open(os.path.join(fx, "srs_cases.json")))
for c in data["cases"]:
    card = r.CardState(**c["card"])
    got = asdict(r.next_state(card, c["grade"], c["now_ms"]))
    check("srs:" + c["name"], got, c["expected"])

d = json.load(open(os.path.join(fx, "afk_cases.json")))
for c in d["cases"]:
    events = [(t, typ) for t, typ in c["events"]]
    got = r.active_ms_from_events(events, c["task_end_ms"])
    check("afk:" + c["name"], got, c["expected_active_ms"])

d = json.load(open(os.path.join(fx, "pace_cases.json")))
for c in d["cases"]:
    pace = r.PaceStats(samples={k: list(v) for k, v in c["samples"].items()})
    got = r.judge_task("K", c["active_ms"], pace)
    check("pace:" + c["name"], got, c["expected"])

sys.exit(1 if fails else 0)
