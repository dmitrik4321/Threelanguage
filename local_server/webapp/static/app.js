"use strict";
/* Threelanguage веб-клиент — полный перенос build/app.py на HTML+CSS+JS.
 *
 * Равноправный клиент «общего ядра» (docs/ARCHITECTURE.md): все решения
 * (SRS, АФК-темп, скоринг, placement, partial_credit) принимает window.TLCore
 * — JS-порт эталонного shared/algorithm/reference.py. Никакой логики оценок
 * здесь нет, только связки UI -> ядро -> состояние.
 *
 * Состояние: localStorage["tl_state"] в формате default_state() из app.py
 * (тот же ключ account{username,token}, что и десктоп), поэтому push/pull
 * совместимы между десктопом и вебом один в один.
 *
 * Идентификация: каждый fetch шлёт заголовок X-Client: web — сервер пишет
 * этот трафик в лог как [client=web] (см. local_server/server.py before/after).
 */

const C = window.TLCore;
const LANG_NAMES = { "en": "English", "pt-BR": "Português", "zh": "中文" };
const LANG_COLORS = { "en": "#5aa7ff", "pt-BR": "#3ecf8e", "zh": "#ff6b6b" };
const FEEDBACK_MS = 900;
const IDLE_SLEEP_S = 90;          // сонный дракон при простое (как watchdog в app.py)

// ---------------------------------------------------------------- state ----
function defaultState() {
  return { day: today(), cards: {}, pace: { samples: {} },
    minutes: Object.fromEntries(Object.keys(LANG_NAMES).map(l => [l, 0.0])),
    history: [], placement_done: false, level: null,
    settings: { theme: "dark", ui_lang: "ru" },
    account: { username: null, token: null } };
}
function today() { return new Date().toISOString().slice(0, 10); }
function loadState() {
  try { return Object.assign(defaultState(), JSON.parse(localStorage.getItem("tl_state") || "{}")); }
  catch (e) { return defaultState(); }
}
function saveState(st) { localStorage.setItem("tl_state", JSON.stringify(st)); }
function rollDay(st) {
  if (st.day !== today()) { st.day = today();
    for (const l of Object.keys(LANG_NAMES)) st.minutes[l] = 0.0; }
}

var st = loadState(); rollDay(st); saveState(st);   // var (не const): mobile-обёртка v2 может переключить ссылку
let sessionAnswers = [];
let tasks = [], taskI = 0, currentTask = null, langOfCurrent = null;
let plResponses = [];
let lock = false;
let taskEvents = [], taskStartWall = 0, lastInputT = 0;

// ------------------------------------------------------------- api (web) ----
async function api(path, method = "GET", body = null, auth = true) {
  // __TL_CLIENT__ ставит mobile.js: 'web-mobile'; по умолчанию десктопная v1 -> 'web'.
  const headers = { "Content-Type": "application/json", "X-Client": window.__TL_CLIENT__ || "web" };
  if (auth && st.account.token) headers.Authorization = "Bearer " + st.account.token;
  const r = await fetch(path, { method, headers, body: body === null ? undefined : JSON.stringify(body) });
  let j = {}; try { j = await r.json(); } catch (e) { /* не-json ответ */ }
  return { status: r.status, json: j };
}

// --------------------------------------------------------------- helpers ----
const $ = sel => document.querySelector(sel);
const content = $("#content");
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  Object.assign(e, attrs);
  for (const k of kids) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}
function toast(msg, color, ms = 2200) {
  const t = $("#toast");
  t.textContent = msg; t.style.color = color; t.classList.remove("hidden");
  clearTimeout(toast._j); toast._j = setTimeout(() => t.classList.add("hidden"), ms);
}
function mood(name, label) {
  $("#dragon").src = "/mascot/dragon_" + name + ".png";
  $("#mood").textContent = label || "";
}
function setTheme(name) { document.body.dataset.theme = name; st.settings.theme = name; saveState(st); }

function updateDashboard() {
  $("#level-box").textContent = "Уровень: " + (st.level ?? "— (нужен тест)");
  const bars = $("#minute-bars"); bars.innerHTML = "";
  for (const [lang, name] of Object.entries(LANG_NAMES)) {
    const m = st.minutes[lang] || 0;
    bars.append(el("div", { className: "dim" }, name),
      (() => { const b = el("div", { className: "bar" });
        b.append(el("i", { style: `width:${Math.min(100, m / 20 * 100)}%;background:${LANG_COLORS[lang]}` }));
        return b; })());
  }
  const hist = st.history || [];
  $("#stat-box").textContent = `уроков: ${hist.length} · лучший: ${Math.max(0, ...hist.map(h => h.score))}`;
  $("#whoami").textContent = st.account.username ? "👤 " + st.account.username : "гость (локально)";
}

// ----------------------------------------------------------- живой контент ----
async function fetchLesson() {
  try {
    const { json } = await api("/api/content/lesson?langs=en,pt-BR,zh", "GET", null, false);
    if (json && json.tasks && json.tasks.length) return json;
  } catch (e) { /* офлайн — fallback ниже */ }
  return FALLBACK_LESSON;
}
async function fetchPlacement() {
  try {
    const { json } = await api("/api/content/placement?lang=pt-BR&langs=en,pt-BR,zh", "GET", null, false);
    if (json && json.items && json.items.length >= 6) return json.items;
  } catch (e) { }
  return FALLBACK_PLACEMENT;
}

// Офлайн-заглушки = copy of build/content.py (LESSON / PLACEMENT_ITEMS)
const FALLBACK_LESSON = { id: "l001", title: "Первые слова (офлайн)", languages: ["en", "pt-BR", "zh"],
  tasks: [
    { kind: "CHOICE_T2T", lang: "pt-BR", prompt: "Как переводится «Hi»?", options: ["Привет", "Пока", "Корова"], answer: 0 },
    { kind: "CHOICE_T2T", lang: "zh", prompt: "Как переводится «你好»?", options: ["Привет", "Пока", "Корова"], answer: 0 },
    { kind: "TYPE_EN2RU", lang: "en", prompt: "Переведи на русский: apple", expected: ["яблоко"] },
    { kind: "TYPE_EN2RU", lang: "en", prompt: "Переведи на русский: goodbye", expected: ["пока", "до свидания"] },
    { kind: "CHOICE_R2T", lang: "pt-BR", prompt: "Что значит «obrigado»?", options: ["спасибо", "пожалуйста", "привет"], answer: 0 },
    { kind: "TYPE_ZH2RU", lang: "zh", prompt: "Переведи: 牛", expected: ["корова", "говядина"] },
    { kind: "CHOICE_T2T", lang: "zh", prompt: "Как переводится «再见»?", options: ["Привет", "Пока", "Корова"], answer: 1 },
    { kind: "TYPE_PT2RU", lang: "pt-BR", prompt: "Переведи: tchau", expected: ["пока"] },
    { kind: "CHOICE_R2T", lang: "en", prompt: "Что значит «cow»?", options: ["корова", "кошка", "курица"], answer: 0 },
    { kind: "LISTEN_CHOOSE", lang: "pt-BR", prompt: "(аудио) tchau — это…", options: ["пока", "привет", "спасибо"], answer: 0 },
  ] };
const FALLBACK_PLACEMENT = [
  { id: "p1", level: 1, prompt: "«Hello» — это…", options: ["привет", "пока", "корова"], answer: 0 },
  { id: "p2", level: 1, prompt: "«牛» — это…", options: ["корова", "кошка", "дом"], answer: 0 },
  { id: "p3", level: 2, prompt: "«obrigado» — это…", options: ["спасибо", "извини", "да"], answer: 0 },
  { id: "p4", level: 2, prompt: "Переведи: water", expected: ["вода"] },
  { id: "p5", level: 3, prompt: "Переведи: yesterday", expected: ["вчера"] },
  { id: "p6", level: 3, prompt: "«谢谢» — это…", options: ["спасибо", "извините", "пожалуйста"], answer: 0 },
  { id: "p7", level: 4, prompt: "Переведи: library", expected: ["библиотека"] },
  { id: "p8", level: 4, prompt: "«图书馆» — это…", options: ["библиотека", "магазин", "больница"], answer: 0 },
  { id: "p9", level: 5, prompt: "Переведи: although", expected: ["хотя", "несмотря на то"] },
  { id: "p10", level: 5, prompt: "«虽然» — это…", options: ["хотя", "потому что", "если"], answer: 0 },
  { id: "p11", level: 6, prompt: "Переведи: achievement", expected: ["достижение"] },
  { id: "p12", level: 6, prompt: "«achievement» по-португальски близко к…", options: ["conquista", "aprendizado", "objetivo"], answer: 0 },
];

let lessonLive = FALLBACK_LESSON, placementLive = FALLBACK_PLACEMENT;

// ---------------------------------------------------------------- screens ----
function showHome() {
  content.innerHTML = "";
  const c = el("div", { className: "card" });
  c.append(el("h1", {}, st.placement_done ? "Готов тренироваться" : "Начни с теста уровня"));
  if (!st.placement_done) {
    c.append(el("p", { className: "dim" }, "Placement — адаптивный IRT-тест, 12–24 вопроса."));
    c.append(el("button", { className: "cta", onclick: startPlacement }, "🎯 Пройти тест уровня"));
  }
  c.append(el("button", { className: "cta green", onclick: startLesson }, "▶ Начать урок"));
  if (st.placement_done)
    c.append(el("p", { className: "dim" }, `Уровень ${st.level} · карточек: ${Object.keys(st.cards).length}`));
  content.append(c);
  setNav("home");
}

async function startLesson() {
  // Живой контент строится на сервере из внешних API — это может быть долго.
  // Показываем явный экран загрузки вместо «мёртвого» окна (жалоба: «во-первых
  // долго»); server-side lesson-cache + параллельные HTTP сводят холодный путь
  // к ~секундам, но UI обязан честно ждать и не выглядеть зависшим.
  showLoading("Готовим живой урок…");
  try {
    lessonLive = await fetchLesson();
  } finally { hideLoading(); }
  tasks = lessonLive.tasks; taskI = 0; sessionAnswers = [];
  showTask();
}

async function startPlacement() {
  showLoading("Готовим тест уровня…");
  try {
    placementLive = await fetchPlacement();
  } finally { hideLoading(); }
  tasks = placementLive; taskI = 0; plResponses = [];
  showTask(true);
}

// ---------------------------------------------------------------- loading ----
let _loadingOverlay = null;
function showLoading(text) {
  if (_loadingOverlay) return;
  _loadingOverlay = el("div", { id: "loading-overlay" });
  const box = el("div", { className: "card loadbox" });
  box.append(el("div", { className: "spinner" }));
  box.append(el("p", {}, text || "Загрузка…"));
  box.append(el("p", { className: "dim" }, "контент берётся из открытых словарей — первый запрос может занять до минуты"));
  _loadingOverlay.append(box);
  document.body.append(_loadingOverlay);
}
function hideLoading() {
  if (_loadingOverlay) { _loadingOverlay.remove(); _loadingOverlay = null; }
}

function taskHeader(c, t) {
  c.append(el("div", { className: "dim" }, `${lessonLive.title || "Placement"} · ${taskI + 1} / ${tasks.length}`));
  const bar = el("div", { className: "bar" });
  bar.append(el("i", { style: `width:${(taskI + 1) / tasks.length * 100}%;background:${LANG_COLORS[t.lang] || "#888"}` }));
  c.append(bar);
  if (t.lang) c.append(el("span", { className: "badge", style: `background:${LANG_COLORS[t.lang]}` }, LANG_NAMES[t.lang]));
  c.append(el("p", { style: "font-size:20px;margin:14px 0" }, t.prompt));
}

function showTask(isPlacement = false) {
  content.innerHTML = "";
  if (taskI >= tasks.length) { isPlacement ? finishPlacement() : finishLesson(); return; }
  const t = tasks[taskI];
  currentTask = t; langOfCurrent = t.lang || "en";
  taskEvents = [[0, "focus_in"]]; taskStartWall = Date.now(); lastInputT = taskStartWall;
  lock = false; mood("idle");
  const c = el("div", { className: "card" });
  taskHeader(c, t);
  if (t.options) {
    t.options.forEach((opt, i) => {
      const b = el("button", { className: "pill", id: "opt" + i }, `${i + 1}.  ${opt}`);
      b.onclick = () => submitChoice(i, isPlacement);
      c.append(b);
    });
  } else {
    const inp = el("input", { type: "text", placeholder: "напиши перевод…", id: "typein" });
    inp.onkeydown = e => { noteInput(); if (e.key === "Enter") submitType(isPlacement); };
    inp.oninput = noteInput;
    const btn = el("button", { className: "cta", onclick: () => submitType(isPlacement) }, "Проверить ⏎");
    c.append(inp, btn);
    setTimeout(() => inp.focus(), 30);
  }
  content.append(c);
}

function noteInput() {
  lastInputT = Date.now();
  taskEvents.push([Math.round(lastInputT - taskStartWall), "input"]);
  mood("idle");
}

function submitChoice(idx, isPlacement) {
  if (lock) return;
  noteInput();
  gradeAnswer(idx === currentTask.answer ? 1.0 : 0.0, isPlacement, idx);
}
function submitType(isPlacement) {
  if (lock) return;
  noteInput();
  const given = ($("#typein") || { value: "" }).value;
  gradeAnswer(C.partialCredit(currentTask.expected || [], given), isPlacement, null);
}

function gradeAnswer(correct, isPlacement, clickedIdx) {
  lock = true;
  const endRel = Date.now() - taskStartWall;
  const active = C.activeMsFromEvents(taskEvents, endRel);
  const kind = currentTask.kind || "CHOICE_T2T";
  const pace = { samples: st.pace.samples };
  const verdict = C.judgeTask(kind, active, pace);
  if (verdict.record_to_pace) C.paceAdd(pace, kind, active);
  st.pace.samples = pace.samples;

  if (isPlacement) {
    plResponses.push(correct);
    toast(correct >= 0.999 ? "Верно" : correct > 0 ? `Частично (${correct | 0})` : "Неверно",
      correct >= 0.999 ? "var(--green)" : "var(--red)");
    mood(correct >= 0.999 ? "cheer" : "sad");
    taskI += 1; setTimeout(() => showTask(true), FEEDBACK_MS);
    return;
  }

  const nowMs = Date.now();
  let grade;
  if (verdict.afk_suspect) grade = correct >= 0.999 ? 3 : 1;
  else grade = (correct >= 1 && active < 5000) ? 4 : correct >= 0.999 ? 3 : correct >= 0.5 ? 2 : 1;
  const cid = currentTask.id || `${lessonLive.id}:${taskI}`;
  const card = C.newCard(st.cards[cid]);
  // UserData-журнал сервера: last_grade + реестр слов (см. server.py::_compute_userdata)
  st.cards[cid] = Object.assign(C.nextState(card, grade, nowMs), { last_grade: grade });
  (st.words = st.words || {})[cid] = {
    text: currentTask._tgt_text || currentTask.prompt,
    lang: currentTask.lang, source: currentTask.source, license: currentTask.license };
  sessionAnswers.push({ kind, correct, active_ms: active, afk_excluded: verdict.afk_suspect,
    too_fast: active < 700, hint_used: false, skipped: false, replays: 0, self_corrected: 0 });
  st.minutes[langOfCurrent] = (st.minutes[langOfCurrent] || 0) + verdict.weight_ms / 60000;
  saveState(st); updateDashboard();

  if (verdict.afk_suspect) {
    mood("angry"); toast(`АФК? Вес задания: ${(verdict.weight_ms / 1000).toFixed(1)} с`, "var(--red)");
  } else if (correct >= 0.999) {
    mood("cheer");
    const dueD = (st.cards[cid].due_ms - nowMs) / 86400000;
    toast(`Верно! Повтор через ${dueD.toFixed(1)} дн.`, "var(--green)");
    highlightRight();
  } else if (correct > 0) {
    mood("sad"); toast(`Частично (${Math.round(correct * 100)}%)`, "var(--accent)"); highlightRight();
  } else {
    mood("sad"); toast("Неверно", "var(--red)");
    if (clickedIdx !== null) $("#opt" + clickedIdx)?.classList.add("wrong");
    highlightRight();
  }
  taskI += 1; setTimeout(() => showTask(false), FEEDBACK_MS);
}

function highlightRight() {
  if (currentTask.options) $("#opt" + currentTask.answer)?.classList.add("right");
}

function finishLesson() {
  const pace = { samples: st.pace.samples };
  const score = C.scoreLesson(sessionAnswers, pace);
  const grade = C.lessonScoreToGrade(score);
  st.history.push({ lesson: lessonLive.id, score, ts: Math.round(Date.now() / 1000), n: sessionAnswers.length });
  saveState(st); updateDashboard();
  content.innerHTML = "";
  mood(score >= 70 ? "cheer" : "sad");
  const c = el("div", { className: "card" });
  c.append(el("h1", {}, "Урок заверён " + ["😞", "🙂", "😀", "🏆"][grade - 1]));
  c.append(el("div", { className: "score-ring", style: `color:${score >= 70 ? "var(--green)" : "var(--accent)"}` }, String(score)),
    el("span", { className: "dim" }, " / 100"));
  const nAfk = sessionAnswers.reduce((s, a) => s + (a.afk_excluded ? 1 : 0), 0);
  const acc = sessionAnswers.reduce((s, a) => s + a.correct, 0) / Math.max(1, sessionAnswers.length);
  for (const [lbl, v] of [["Заданий", sessionAnswers.length], ["Точность", Math.round(acc * 100) + "%"],
                          ["АФК-подозрений", nAfk], ["Грейд урока", grade]])
    c.append(el("span", { className: "kv" }, el("b", {}, String(v)), el("span", { className: "dim" }, lbl)));
  c.append(el("p", { className: "dim" }, Object.entries(st.minutes).map(([l, m]) => `${LANG_NAMES[l]}: ${m.toFixed(1)} мин`).join(" · ")));
  c.append(el("button", { className: "cta green", onclick: startLesson }, "↻ Ещё раз"),
           el("button", { className: "cta", onclick: showHome }, "Домой"));
  content.append(c);
}

function finishPlacement() {
  const res = C.placementRun(tasks, plResponses);
  st.placement_done = true; st.level = res.assigned_level;
  saveState(st); updateDashboard();
  content.innerHTML = "";
  mood("cheer");
  const c = el("div", { className: "card" });
  c.append(el("h1", {}, `Твой уровень: ${res.assigned_level} из 6`),
    el("p", { className: "dim" }, `θ=${res.theta} · вопросов использовано: ${res.items_used}`),
    el("button", { className: "cta green", onclick: startLesson }, "▶ Первый урок"));
  content.append(c);
}

// -------------------------------------------------------------- settings ----
function showSettings() {
  content.innerHTML = "";
  const c = el("div", { className: "card" });
  c.append(el("h1", {}, "⚙ Настройки"));
  c.append(el("div", { className: "sec" }, "Тема"));
  for (const th of ["dark", "light"])
    c.append(el("button", { className: "cta" + (st.settings.theme === th ? " green" : ""),
      onclick: () => { setTheme(th); showSettings(); } }, th));
  c.append(el("div", { className: "sec" }, "Аккаунт (синхронизация с локальным сервером)"));
  const u = el("input", { type: "text", placeholder: "логин", id: "su" });
  const p = el("input", { type: "password", placeholder: "пароль", id: "sp" });
  c.append(u, p);
  c.append(el("button", { className: "cta blue", onclick: () => doAuth("/api/auth/register") }, "Регистрация"),
           el("button", { className: "cta blue", onclick: () => doAuth("/api/auth/login") }, "Вход"),
           el("button", { className: "ghost", onclick: logout }, "Выйти"));
  c.append(el("div", { className: "sec" }, "Сброс"));
  c.append(el("button", { className: "ghost", onclick: () => { st.pace.samples = {}; saveState(st); toast("Темп сброшен", "var(--accent)"); } }, "Сбросить скорость"));
  c.append(el("button", { className: "ghost", onclick: () => {
    if (confirm("Стереть весь прогресс?")) {
      const keep = st.account; Object.assign(st, defaultState()); st.account = keep;
      saveState(st); updateDashboard(); showSettings(); toast("Прогресс сброшен", "var(--red)");
    } } }, "Сбросить прогресс"));
  c.append(el("div", { className: "sec" }, "Язык интерфейса (языковые пакеты — заглушка, см. lang_packs/)"));
  const sel = el("select", { class: "ghost" });
  for (const code of PACK_UI) sel.append(el("option", { value: code }, code));
  sel.value = st.settings.ui_lang || "ru";
  sel.onchange = () => { st.settings.ui_lang = sel.value; saveState(st); };
  c.append(sel);
  content.append(c);
  setNav("settings");
}
const PACK_UI = ["ru"];   // скан папок lang_packs делает сервер (/api/packs), см. ниже

async function doAuth(path) {
  const { json } = await api(path, "POST",
    { username: $("#su").value.trim(), password: $("#sp").value }, false);
  if (json.token) {
    st.account = { username: json.username, token: json.token };
    saveState(st); updateDashboard();
    toast("Вход выполнен: " + json.username, "var(--green)");
  } else toast(json.error || "ошибка", "var(--red)");
}
function logout() {
  st.account = { username: null, token: null };
  saveState(st); updateDashboard(); toast("Вышли", "var(--dim)");
}

$("#btn-sync").onclick = async () => {
  if (!st.account.token) { toast("Сначала войди в аккаунт (Настройки)", "var(--red)"); return; }
  const push = await api("/api/sync/push", "POST", st);
  if (push.json.ok) {
    const pull = await api("/api/sync/pull");
    if (pull.json.state) {
      const remote = pull.json.state;
      if ((remote.history || []).length >= (st.history || []).length) {
        Object.assign(st, remote); saveState(st);
      }
    }
    // UserData-журнал сервера: сводка по словам (сколько знаем/ошибались).
    // Push отвечает {userdata:{words,known,errors}} — показываем её в тосте.
    const ud = push.json.userdata;
    updateDashboard(); toast(ud ? `Синхронизировано ✓ · слов ${ud.words}, известно ${ud.known}` : "Синхронизировано ✓", "var(--green)");
  } else toast(push.json.error || "сервер недоступен", "var(--red)");
};

// ------------------------------------------------------ AFK-темп-монитор ----
let idleSince = Date.now();
document.addEventListener("visibilitychange", () => {
  taskEvents.push([Math.round(Date.now() - taskStartWall),
    document.hidden ? "focus_out" : "focus_in"]);
});
for (const ev of ["pointerdown", "keydown"])
  document.addEventListener(ev, () => { idleSince = Date.now(); });
setInterval(() => {
  // AFK-визуал: сонный дракон. "_idle" — отладочная подпись, в UI её быть не
  // должно (замечание Квена Старшего): показываем человекочитаемо и только
  // когда пользователь реально бездействует.
  if (Date.now() - idleSince > IDLE_SLEEP_S * 1000) mood("sleep", "нет активности");
}, 5000);

// ---------------------------------------------------------------- boot ----
function setNav(name) {
  for (const b of document.querySelectorAll(".navb"))
    b.classList.toggle("active", b.dataset.nav === name);
}
for (const b of document.querySelectorAll(".navb"))
  b.onclick = () => (b.dataset.nav === "home" ? showHome() : showSettings());

(async function boot() {
  setTheme(st.settings.theme || "dark");
  updateDashboard();
  showHome();
  try {   // список языковых пакетов с сервера (заглушка UI-локализации)
    const { json } = await api("/api/packs", "GET", null, false);
    if (json && json.app_ui) PACK_UI.length = 0, PACK_UI.push(...json.app_ui);
  } catch (e) { }
  lessonLive = await fetchLesson();     // прогрев кэша живого контента
})();
