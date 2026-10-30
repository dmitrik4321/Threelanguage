"""Threelanguage core — эталонная реализация алгоритмов (v0.1).

Единый источник истины для Kotlin- и C++-портов ядра. Любая платформа обязана
давать побитово близкие результаты на fixtures/*.json (допуск 1e-6).
Модули: srs, afk, scoring, placement, session.
"""
from __future__ import annotations
import json, math, unicodedata
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

# ------------------------------------------------------------------ SRS ----
LN09 = math.log(0.9)

@dataclass
class CardState:
    stability: float = 0.0          # дни
    difficulty: float = 5.0         # 1..10
    due_ms: int = 0                 # unix ms
    reps: int = 0
    lapses: int = 0
    state: str = "new"              # new|learning|review|relearn

W = dict(
    s0=(0.4, 1.4, 3.2, 15.2),      # начальная стабильность по грейдам Again..Easy
    d0_center=6.6,
    recall_a=0.1, recall_b=0.15, recall_c=0.9,
    mult={"hard": 0.6, "good": 1.0, "easy": 1.6},
    lapse_factor=0.5, lapse_d=0.4, lapse_s=0.6, lapse_r=0.65,
    max_interval_days=365.0,
)

def retention(t_days: float, s: float) -> float:
    if s <= 0: return 0.0
    return math.exp(-t_days * -LN09 / s)   # R = 0.9^(t/S)

def grade_to_score(g: int) -> float: ...  # placeholder removed below

def next_state(card: CardState, grade: int, now_ms: int,
               desired_retention: float = 0.9,
               lang_difficulty_bias: float = 0.0) -> CardState:
    """grade: 1 Again, 2 Hard, 3 Good, 4 Easy. Возвращает НОВУЮ карточку."""
    c = CardState(**asdict(card))
    elapsed_days = max(0.0, (now_ms - card.due_ms) / 86_400_000.0) if card.reps else 0.0
    R = retention(elapsed_days, card.stability) if card.state in ("review", "relearn") else 0.0

    if card.state == "new":
        c.stability = W["s0"][grade - 1]
        c.difficulty = min(10.0, max(1.0, W["d0_center"] - (grade - 3) + lang_difficulty_bias))
        c.state = "review" if grade >= 3 else "learning"
        c.reps += 1
    elif grade == 1:  # lapse
        c.lapses += 1
        c.stability = max(0.3, W["lapse_factor"] * (c.difficulty ** -W["lapse_d"])
                          * ((c.stability + 1) ** W["lapse_s"] - 1)
                          * math.exp((1 - R) * W["lapse_r"]))
        c.difficulty = min(10.0, c.difficulty + 1.0)
        c.state = "relearn"
        c.reps += 1
    else:
        mult = W["mult"]["hard" if grade == 2 else "good" if grade == 3 else "easy"]
        d_new = min(10.0, max(1.0, c.difficulty - 0.2 * (grade - 3) + 0.1))
        bump = math.exp(W["recall_a"]) * (11 - d_new) * (c.stability ** -W["recall_b"]) \
               * (math.exp(W["recall_c"] * (1 - R)) - 1) * mult
        c.stability *= (1 + bump)
        c.difficulty = d_new
        if c.state in ("learning", "relearn") and grade >= 3:
            c.state = "review"
        c.reps += 1

    # due
    if c.state in ("learning", "relearn"):
        step_ms = 600_000 if grade == 1 else 86_400_000 // 2
        c.due_ms = now_ms + step_ms
    else:
        t_days = c.stability * (math.log(desired_retention) / LN09)
        t_days = min(t_days, W["max_interval_days"])
        c.due_ms = now_ms + int(t_days * 86_400_000)
    return c

# ------------------------------------------------------------------ AFK ----
AFK = dict(ping_window_ms=20_000, idle_cut_ms=25_000, pace_multiplier=10,
           min_clean_samples=5, ceiling_ms=600_000, micro_floor_ms=120_000,
           window_n=30)

@dataclass
class PaceStats:
    samples: Dict[str, List[int]] = field(default_factory=dict)  # kind -> active_ms чистых

    def median(self, kind: str) -> Optional[int]:
        arr = sorted(self.samples.get(kind, []))
        n = len(arr)
        if n == 0: return None
        m = n // 2
        return arr[m] if n % 2 else (arr[m - 1] + arr[m]) // 2

    def percentile(self, kind: str, p: float) -> Optional[int]:
        arr = sorted(self.samples.get(kind, []))
        if not arr: return None
        k = max(0, min(len(arr) - 1, int(round((len(arr) - 1) * p))))
        return arr[k]

    def add(self, kind: str, active_ms: int):
        self.samples.setdefault(kind, [])
        arr = self.samples[kind]
        arr.append(active_ms)
        if len(arr) > AFK["window_n"]: del arr[: len(arr) - AFK["window_n"]]

def active_ms_from_events(events: List[tuple], task_end_ms: int) -> int:
    """events: [(t_rel_ms, type)] type in {input, focus_out, focus_in}.
    Активное время: интервалы фокуса минус куски простоя длиннее idle_cut_ms.
    Простой = разрыв между событиями ввода (или от focus_out до конца задания)."""
    total = 0
    last_event = 0
    focused = True
    for t, typ in events:
        t = min(t, task_end_ms)
        gap = t - last_event
        if focused and gap > AFK["idle_cut_ms"]:
            total += max(0, AFK["idle_cut_ms"])   # учитываем только «живую» часть разрыва
        elif focused:
            total += max(0, gap)
        if typ == "focus_out":
            focused = False
        elif typ == "focus_in":
            focused = True
            last_event = t
            continue
        last_event = t
    if focused:
        gap = task_end_ms - last_event
        total += max(0, min(gap, AFK["idle_cut_ms"])) if gap > AFK["idle_cut_ms"] else max(0, gap)
    return total

def judge_task(kind: str, active_ms: int, pace: PaceStats) -> dict:
    med = pace.median(kind)
    if med is None or len(pace.samples.get(kind, [])) < AFK["min_clean_samples"]:
        limit = AFK["ceiling_ms"]
    else:
        limit = max(AFK["pace_multiplier"] * med, AFK["micro_floor_ms"])
    suspect = active_ms > limit
    weight = med if (suspect and med is not None) else active_ms
    return {"afk_suspect": suspect, "weight_ms": weight,
            "record_to_pace": not suspect}

def suspect_autoclick(input_ts: List[int]) -> bool:
    if len(input_ts) < 8: return False
    deltas = [b - a for a, b in zip(input_ts, input_ts[1:])]
    mean = sum(deltas) / len(deltas)
    if mean <= 0: return True
    var = sum((d - mean) ** 2 for d in deltas) / len(deltas)
    return (var ** 0.5) / mean < 0.15

# ------------------------------------------------------------- SCORING ----
@dataclass
class AnswerRecord:
    kind: str
    correct: float          # 0..1 частичная (levenshtein/NFKC)
    active_ms: int
    afk_excluded: bool
    hint_used: bool = False
    too_fast: bool = False
    self_corrected: int = 0
    skipped: bool = False
    replays: int = 0

def _entropy_norm(vals: List[float]) -> float:
    if not vals: return 0.0
    tot = sum(vals) or 1.0
    ps = [v / tot for v in vals]
    h = -sum(p * math.log(p) for p in ps if p > 0)
    return h / math.log(len(ps)) if len(ps) > 1 else 0.0

def score_lesson(answers: List[AnswerRecord], pace: PaceStats) -> int:
    if not answers: return 0
    n = len(answers)
    # A accuracy
    acc = [a.correct * (0.7 if a.hint_used else 1.0) for a in answers]
    A = sum(acc) / n
    # B speed-vs-pace (только не-AFK)
    bvals = []
    for a in answers:
        if a.afk_excluded: continue
        p75 = pace.percentile(a.kind, 0.75) or 15_000
        r = a.active_ms / max(1, p75)
        bvals.append(1.0 if r <= 1 else max(0.0, 1 - 0.7 * (r - 1) / 2) if r <= 3 else 0.0)
        if a.too_fast: bvals[-1] *= 0.5
    B = sum(bvals) / len(bvals) if bvals else 0.5
    # C consistency: предсказуемость качества между заданиями похожей сложности
    C = 1.0 - _entropy_norm([x * 10 + 1 for x in acc]) if n >= 4 else 0.7
    # D effort
    dvals = []
    for a in answers:
        v = 1.0
        if a.skipped: v -= 0.8
        if a.replays > 2: v -= 0.1 * (a.replays - 2)
        v += min(0.15, 0.05 * a.self_corrected)
        dvals.append(max(0.0, min(1.0, v)))
    D = sum(dvals) / n
    score = round(100 * (0.45 * A + 0.15 * B + 0.20 * C + 0.20 * D))
    return max(0, min(100, score))

def lesson_score_to_grade(score: int) -> int:
    return 1 if score < 45 else 2 if score < 70 else 3 if score < 90 else 4

# ------------------------------------------------------------ PLACEMENT ----
def irt_theta_update(theta: float, b: float, y: float, eta: float = 0.5) -> float:
    p = 1 / (1 + math.exp(-(theta - b)))
    return theta + eta * (y - p)

def placement_run(items: List[dict], responses: List[float],
                  max_items: int = 24, se_stop: float = 0.3) -> dict:
    """items: [{id, level(1..6)}]; responses: 0..1 correctness в порядке выдачи."""
    theta = 3.5
    used = []
    for i, (item, y) in enumerate(zip(items, responses)):
        b = item["level"] - 0.5
        theta = irt_theta_update(theta, b, y)
        used.append(item["id"])
        info = sum(_p(theta, it["level"] - 0.5) * (1 - _p(theta, it["level"] - 0.5)) for it in items[:i+1])
        if info and 1 / math.sqrt(info) < se_stop and i + 1 >= 16: break
        if i + 1 >= max_items: break
    return {"theta": round(theta, 3), "assigned_level": max(1, min(6, round(theta))),
            "items_used": len(used)}

def _p(theta, b): return 1 / (1 + math.exp(-(theta - b)))

# --------------------------------------------------------------- TEXT ----
def normalize_answer(s: str) -> str:
    s = unicodedata.normalize("NFKC", s.strip().casefold())
    return "".join(ch for ch in unicodedata.normalize("NFD", s)
                   if unicodedata.category(ch) != "Mn") if all(ord(c) < 0x3000 for c in s) else s

def levenshtein(a: str, b: str) -> int:
    if len(a) < len(b): a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]

def partial_credit(expected: List[str], given: str) -> float:
    g = normalize_answer(given)
    best = 0.0
    for e in expected:
        e_n = normalize_answer(e)
        if g == e_n: return 1.0
        d = levenshtein(g, e_n)
        credit = max(0.0, 1 - d / max(1, len(e_n)))
        best = max(best, credit if d <= 1 else credit * 0.6)
    return round(best, 4)

if __name__ == "__main__":
    # быстрый smoke
    c = CardState()
    c = next_state(c, 3, 1_700_000_000_000)
    print("after Good:", c)
    pace = PaceStats(samples={"CHOICE_R2T": [1200, 1500, 1100, 1400, 1300, 1250]})
    print(judge_task("CHOICE_R2T", 90_000, pace))
    print(partial_credit(["apple"], "appla"), partial_credit(["ni hao 你好"], "你好"))
