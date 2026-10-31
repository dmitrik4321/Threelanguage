#!/usr/bin/env python3
"""Паритет shared/algorithm/web_core.js против эталонного reference.py.

Прогоняет те же fixtures (shared/algorithm/fixtures/*.json) через node и
сравнивает результаты с Python-эталоном case-by-case, с той же tolerance,
что и run_fixtures.py. Выход: PASS/FAIL на кейс; код 0 — паритет полный.

Формат fixtures (общий для обоих раннеров):
  srs_cases.json   : {tolerance, cases:[{name, card, grade, now_ms, expected}]}
  afk_cases.json   : {tolerance, cases:[{name, events, task_end_ms, expected_active_ms, verdict?}]}
  pace_cases.json  : {cases:[{name, samples, active_ms, expected:{afk_suspect, weight_ms, record_to_pace}}]}
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "shared" / "algorithm" / "fixtures"
RUNNER_JS = Path(__file__).resolve().parent / "web_run_fixtures_v2.js"

sys.path.insert(0, str(ROOT / "shared" / "algorithm"))
import reference as ref  # noqa: E402


def close(a, b, tol):
    if isinstance(a, dict):
        return all(k in b and close(a[k], b[k], tol) for k in a)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= tol + 1e-12
    return a == b


def py_srs(case):
    c = case["card"]
    card = ref.CardState(stability=c["stability"], difficulty=c["difficulty"],
                        due_ms=c["due_ms"], reps=c["reps"], lapses=c["lapses"],
                        state=c["state"])
    out = ref.next_state(card, case["grade"], case["now_ms"])
    return out.__dict__ if hasattr(out, "__dict__") else out


def py_afk(case):
    out = {"active_ms": ref.active_ms_from_events(case["events"], case["task_end_ms"])}
    pace = ref.PaceStats(samples=case.get("samples") or {})
    out["verdict"] = ref.judge_task(case.get("kind", "typing"), out["active_ms"], pace)
    return out


def py_pace(case):
    pace = ref.PaceStats(samples=case.get("samples") or {})
    d = ref.judge_task(case.get("kind", "typing"), case["active_ms"], pace)
    return {"afk_suspect": d["afk_suspect"], "weight_ms": d["weight_ms"],
            "record_to_pace": d["record_to_pace"]}


def main():
    files = sorted(FIXTURES.glob("*.json"))
    proc = subprocess.run(
        ["node", str(RUNNER_JS)] + [str(f) for f in files],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print("NODE RUNNER FAILED:\n" + proc.stderr, file=sys.stderr)
        return 2
    js_all = json.loads(proc.stdout)

    fails = 0
    total = 0
    for f in files:
        fx = json.load(open(f))
        tol = fx.get("tolerance", 1e-6)
        js_out = js_all[str(f)]
        for case in fx["cases"]:
            total += 1
            name = case["name"]
            if f.name == "srs_cases.json":
                exp = py_srs(case)
                got = js_out["srs"].get(name)
            elif f.name == "afk_cases.json":
                exp = py_afk(case)
                got = js_out["afk"].get(name)
            elif f.name == "pace_cases.json":
                exp = py_pace(case)
                got = js_out["pace"].get(name)
            else:
                continue
            ok = got is not None and close(exp, got, tol)
            print(("PASS " if ok else "FAIL ") + f"{f.name}:{name}")
            if not ok:
                fails += 1
                print("  expected:", json.dumps(exp, sort_keys=True))
                print("  js      :", json.dumps(got, sort_keys=True) if got is not None else "None")
    print(f"\nWEB PARITY: {total - fails}/{total} cases match reference.py")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
