"use strict";
/* Threelanguage core — JS-порт эталонного shared/algorithm/reference.py (v0.1).
 *
 * Побитовый паритет с Python-эталоном: те же константы W/AFK, та же арифметика
 * (IEEE754 double в обоих языках), те же формулы SRS / AFK / scoring / placement /
 * partial_credit. Проверка паритета — local_server/tests/test_web_parity.py
 * (прогоняет fixtures/*.json через node и сравнивает с reference.py, допуск 1e-6).
 *
 * Экспорт: window.TLCore (браузер) или module.exports (node).
 */

// ------------------------------------------------------------------ SRS ----
const LN09 = Math.log(0.9);
const W = {
  s0: [0.4, 1.4, 3.2, 15.2],          // начальная стабильность по грейдам Again..Easy
  d0_center: 6.6,
  recall_a: 0.1, recall_b: 0.15, recall_c: 0.9,
  mult: { hard: 0.6, good: 1.0, easy: 1.6 },
  lapse_factor: 0.5, lapse_d: 0.4, lapse_s: 0.6, lapse_r: 0.65,
  max_interval_days: 365.0,
};

function newCard(partial) {
  const c = Object.assign({ stability: 0.0, difficulty: 5.0, due_ms: 0,
    reps: 0, lapses: 0, state: "new" }, partial || {});
  return c;
}

function retention(tDays, s) {
  if (s <= 0) return 0.0;
  return Math.exp(-tDays * -LN09 / s);   // R = 0.9^(t/S)
}

// Python round() — banker's rounding; обычный Math.round округляет .5 вверх.
function pyRound(x) {
  const f = Math.floor(x), d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return (f % 2 === 0) ? f : f + 1;
}

function nextState(card, grade, nowMs, desiredRetention = 0.9, langDifficultyBias = 0.0) {
  const c = newCard(card);                 // копия, как CardState(**asdict(card))
  const elapsedDays = card.reps ? Math.max(0.0, (nowMs - card.due_ms) / 86400000.0) : 0.0;
  const R = (card.state === "review" || card.state === "relearn")
    ? retention(elapsedDays, card.stability) : 0.0;

  if (card.state === "new") {
    c.stability = W.s0[grade - 1];
    c.difficulty = Math.min(10.0, Math.max(1.0, W.d0_center - (grade - 3) + langDifficultyBias));
    c.state = grade >= 3 ? "review" : "learning";
    c.reps += 1;
  } else if (grade === 1) {                // lapse
    c.lapses += 1;
    c.stability = Math.max(0.3, W.lapse_factor * Math.pow(c.difficulty, -W.lapse_d)
      * (Math.pow(c.stability + 1, W.lapse_s) - 1)
      * Math.exp((1 - R) * W.lapse_r));
    c.difficulty = Math.min(10.0, c.difficulty + 1.0);
    c.state = "relearn";
    c.reps += 1;
  } else {
    const mult = W.mult[grade === 2 ? "hard" : grade === 3 ? "good" : "easy"];
    const dNew = Math.min(10.0, Math.max(1.0, c.difficulty - 0.2 * (grade - 3) + 0.1));
    const bump = Math.exp(W.recall_a) * (11 - dNew) * Math.pow(c.stability, -W.recall_b)
      * (Math.exp(W.recall_c * (1 - R)) - 1) * mult;
    c.stability *= (1 + bump);
    c.difficulty = dNew;
    if ((c.state === "learning" || c.state === "relearn") && grade >= 3) c.state = "review";
    c.reps += 1;
  }

  if (c.state === "learning" || c.state === "relearn") {
    const stepMs = grade === 1 ? 600000 : Math.floor(86400000 / 2);
    c.due_ms = nowMs + stepMs;
  } else {
    let tDays = c.stability * (Math.log(desiredRetention) / LN09);
    tDays = Math.min(tDays, W.max_interval_days);
    c.due_ms = nowMs + Math.trunc(tDays * 86400000);   // int() у Python = trunc
  }
  return c;
}

// ------------------------------------------------------------------ AFK ----
const AFK = { ping_window_ms: 20000, idle_cut_ms: 25000, pace_multiplier: 10,
  min_clean_samples: 5, ceiling_ms: 600000, micro_floor_ms: 120000, window_n: 30 };

function paceMedian(pace, kind) {
  const arr = [...(pace.samples[kind] || [])].sort((a, b) => a - b);
  const n = arr.length;
  if (n === 0) return null;
  const m = Math.floor(n / 2);
  return n % 2 ? arr[m] : Math.floor((arr[m - 1] + arr[m]) / 2);   // // в Python = floor div
}

function pacePercentile(pace, kind, p) {
  const arr = [...(pace.samples[kind] || [])].sort((a, b) => a - b);
  if (!arr.length) return null;
  const k = Math.max(0, Math.min(arr.length - 1, pyRound((arr.length - 1) * p)));
  return arr[k];
}

function paceAdd(pace, kind, activeMs) {
  if (!pace.samples[kind]) pace.samples[kind] = [];
  const arr = pace.samples[kind];
  arr.push(activeMs);
  if (arr.length > AFK.window_n) arr.splice(0, arr.length - AFK.window_n);
}

function activeMsFromEvents(events, taskEndMs) {
  let total = 0, lastEvent = 0, focused = true;
  for (const [tRaw, typ] of events) {
    const t = Math.min(tRaw, taskEndMs);
    const gap = t - lastEvent;
    if (focused && gap > AFK.idle_cut_ms) total += Math.max(0, AFK.idle_cut_ms);
    else if (focused) total += Math.max(0, gap);
    if (typ === "focus_out") focused = false;
    else if (typ === "focus_in") { focused = true; lastEvent = t; continue; }
    lastEvent = t;
  }
  if (focused) {
    const gap = taskEndMs - lastEvent;
    total += gap > AFK.idle_cut_ms ? Math.max(0, Math.min(gap, AFK.idle_cut_ms)) : Math.max(0, gap);
  }
  return total;
}

function judgeTask(kind, activeMs, pace) {
  const med = paceMedian(pace, kind);
  let limit;
  if (med === null || (pace.samples[kind] || []).length < AFK.min_clean_samples) {
    limit = AFK.ceiling_ms;
  } else {
    limit = Math.max(AFK.pace_multiplier * med, AFK.micro_floor_ms);
  }
  const suspect = activeMs > limit;
  const weight = (suspect && med !== null) ? med : activeMs;
  return { afk_suspect: suspect, weight_ms: weight, record_to_pace: !suspect };
}

function suspectAutoclick(inputTs) {
  if (inputTs.length < 8) return false;
  const deltas = [];
  for (let i = 1; i < inputTs.length; i++) deltas.push(inputTs[i] - inputTs[i - 1]);
  const mean = deltas.reduce((a, b) => a + b, 0) / deltas.length;
  if (mean <= 0) return true;
  const variance = deltas.reduce((s, d) => s + (d - mean) * (d - mean), 0) / deltas.length;
  return Math.sqrt(variance) / mean < 0.15;
}

// ------------------------------------------------------------- SCORING ----
function entropyNorm(vals) {
  if (!vals.length) return 0.0;
  const tot = vals.reduce((a, b) => a + b, 0) || 1.0;
  const ps = vals.map(v => v / tot);
  let h = 0;
  for (const p of ps) if (p > 0) h -= p * Math.log(p);
  return ps.length > 1 ? h / Math.log(ps.length) : 0.0;
}

function scoreLesson(answers, pace) {
  if (!answers.length) return 0;
  const n = answers.length;
  const acc = answers.map(a => a.correct * (a.hint_used ? 0.7 : 1.0));
  const A = acc.reduce((x, y) => x + y, 0) / n;
  const bvals = [];
  for (const a of answers) {
    if (a.afk_excluded) continue;
    const p75 = pacePercentile(pace, a.kind, 0.75) || 15000;
    const r = a.active_ms / Math.max(1, p75);
    bvals.push(r <= 1 ? 1.0 : (r <= 3 ? Math.max(0.0, 1 - 0.7 * (r - 1) / 2) : 0.0));
    if (a.too_fast) bvals[bvals.length - 1] *= 0.5;
  }
  const B = bvals.length ? bvals.reduce((x, y) => x + y, 0) / bvals.length : 0.5;
  const C = n >= 4 ? 1.0 - entropyNorm(acc.map(x => x * 10 + 1)) : 0.7;
  const dvals = answers.map(a => {
    let v = 1.0;
    if (a.skipped) v -= 0.8;
    if (a.replays > 2) v -= 0.1 * (a.replays - 2);
    v += Math.min(0.15, 0.05 * (a.self_corrected || 0));
    return Math.max(0.0, Math.min(1.0, v));
  });
  const D = dvals.reduce((x, y) => x + y, 0) / n;
  const score = pyRound(100 * (0.45 * A + 0.15 * B + 0.20 * C + 0.20 * D));
  return Math.max(0, Math.min(100, score));
}

function lessonScoreToGrade(score) {
  return score < 45 ? 1 : score < 70 ? 2 : score < 90 ? 3 : 4;
}

// ------------------------------------------------------------ PLACEMENT ----
function irtThetaUpdate(theta, b, y, eta = 0.5) {
  const p = 1 / (1 + Math.exp(-(theta - b)));
  return theta + eta * (y - p);
}

function _p(theta, b) { return 1 / (1 + Math.exp(-(theta - b))); }

function placementRun(items, responses, maxItems = 24, seStop = 0.3) {
  let theta = 3.5;
  const used = [];
  const m = Math.min(items.length, responses.length);
  for (let i = 0; i < m; i++) {
    const item = items[i], y = responses[i];
    theta = irtThetaUpdate(theta, item.level - 0.5, y);
    used.push(item.id);
    let info = 0;
    for (let j = 0; j <= i; j++) {
      const b = items[j].level - 0.5;
      info += _p(theta, b) * (1 - _p(theta, b));
    }
    if (info && 1 / Math.sqrt(info) < seStop && i + 1 >= 16) break;
    if (i + 1 >= maxItems) break;
  }
  return { theta: Number(pyRound(theta * 1000) / 1000).toFixed(3) * 1,
    assigned_level: Math.max(1, Math.min(6, pyRound(theta))), items_used: used.length };
}

// --------------------------------------------------------------- TEXT ----
// Нормализация ответа: NFKC + casefold + снятие диакритики (для латиницы).
// В браузере String.normalize("NFKC") == unicodedata.normalize("NFKC").
function normalizeAnswer(s) {
  s = s.trim().normalize("NFKC").toLowerCase();
  if ([...s].every(ch => ch.codePointAt(0) < 0x3000)) {
    s = s.normalize("NFD").replace(/\p{Mn}/gu, "");
  }
  return s;
}

function levenshtein(a, b) {
  if (a.length < b.length) { const t = a; a = b; b = t; }
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
    }
    prev = cur;
  }
  return prev[b.length];
}

function partialCredit(expected, given) {
  const g = normalizeAnswer(given);
  let best = 0.0;
  for (const e of expected) {
    const en = normalizeAnswer(e);
    if (g === en) return 1.0;
    const d = levenshtein(g, en);
    const credit = Math.max(0.0, 1 - d / Math.max(1, en.length));
    best = Math.max(best, d <= 1 ? credit : credit * 0.6);
  }
  return Number(pyRound(best * 10000) / 10000);
}

const TLCore = { LN09, W, AFK, newCard, retention, pyRound, nextState,
  paceMedian, pacePercentile, paceAdd, activeMsFromEvents, judgeTask, suspectAutoclick,
  entropyNorm, scoreLesson, lessonScoreToGrade, irtThetaUpdate, placementRun,
  normalizeAnswer, levenshtein, partialCredit };

if (typeof window !== "undefined") window.TLCore = TLCore;
if (typeof module !== "undefined") module.exports = TLCore;
