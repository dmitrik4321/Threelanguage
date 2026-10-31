"use strict";
/* Threelanguage мобильный веб-клиент v2 — адаптация того же кода, что и v1.
 *
 * Честная архитектура: вся бизнес-логика (состояние, API-обвязка с X-Client,
 * живой контент + офлайн-фолбэки, уроки/placement/грейд/АФК через window.TLCore)
 * живёт в app.js и НЕ дублируется здесь. mobile.js только:
 *   1) перекраивает DOM под мобильную раскладку (шаг A),
 *   2) переопределяет навигацию на нижнюю таб-панель (шаг B),
 *   3) добавляет мобильные улучшения поверх готовых функций (шаг C).
 *
 * Порядок загрузки: web_core.js -> app.js -> mobile.js (boot() из app.js к этому
 * моменту уже отработал; свои обработчики nav он навесил на .navb — их больше нет
 * в DOM, поэтому ниже мы вешаем свои).
 */

// --------------------------------------------------------------- шаг A ----
// Десктопный #sidebar выкидываем; его содержимое переезжает:
//  - дракон/настроение -> #topbar (мелкий, рядом с брендом);
//  - минуты/уровень/статистика -> чипы #mini-stats;
//  - whoami/синхронизация -> выдвижной drawer.
(function reparentDom() {
  const side = document.getElementById("sidebar");
  if (!side) return;                      // уже мобильная разметка

  // mood(): app.js пишет label в #mood — даём ему невидимый якорь в topbar
  const moodAnchor = el("span", { id: "mood", className: "hidden" });
  document.getElementById("topbar").append(moodAnchor);

  // whoami(): app.js обновляет #whoami — переносим узел в drawer как есть
  const who = document.getElementById("whoami");
  if (who) document.querySelector("#drawer .drawer-sheet").prepend(who);

  side.remove();
})();

// Компактный дашборд в шапке вместо сайдбарных секций. Обновляется своим
// подписчиком на saveState (см. хук ниже) — параллельно с updateDashboard().
function updateMiniStats() {
  const box = document.getElementById("mini-stats");
  if (!box) return;
  box.innerHTML = "";
  for (const [lang, name] of Object.entries(LANG_NAMES)) {
    const m = st.minutes[lang] || 0;
    const chip = el("div", { className: "chip" },
      el("b", {}, `${m.toFixed(1)} мин`), document.createTextNode(name));
    const bar = el("div", { className: "bar" });
    bar.append(el("i", { style: `width:${Math.min(100, m / 20 * 100)}%;background:${LANG_COLORS[lang]}` }));
    chip.append(bar);
    box.append(chip);
  }
  const lvl = el("div", { className: "chip" },
    el("b", {}, st.level ? `ур. ${st.level}` : "тест?"),
    document.createTextNode(`уроков: ${(st.history || []).length}`));
  box.append(lvl);
}

// Хук saveState: каждая запись состояния (ответ урока, синк, сброс) обновляет
// и мини-дашборд. Оригинальная функция вызывается первой — поведение v1 целое.
let _saveStateOrig;
_saveStateOrig = saveState;
window.saveState = function (s) { _saveStateOrig(s); updateMiniStats(); };

// --------------------------------------------------------------- шаг B ----
// Навигация: нижняя таб-панель. setNav переопределён — app.js зовёт его из
// showHome()/showSettings(), а boot() уже отработал со старыми .navb.
window.setNav = function (name) {
  for (const b of document.querySelectorAll(".tabb"))
    b.classList.toggle("active", b.dataset.nav === name);
};

document.querySelectorAll(".tabb").forEach(b => {
  b.onclick = () => {
    const nav = b.dataset.nav;
    if (nav === "home") showHome();
    else if (nav === "lesson") startLesson();
    else if (nav === "progress") showProgress();
    else if (nav === "settings") showSettings();
  };
});

// Drawer: меню-гамбургер открывает, «Закрыть»/тап по затемнению закрывают.
const drawer = document.getElementById("drawer");
document.getElementById("btn-menu").onclick = () => drawer.classList.remove("hidden");
document.getElementById("btn-close-drawer").onclick = () => drawer.classList.add("hidden");
drawer.addEventListener("click", e => { if (e.target === drawer) drawer.classList.add("hidden"); });
// Кнопка ⇅ внутри drawer — тот же обработчик, что в v1 (#btn-sync), просто
// после успешного синка закрываем шторку.
const syncBtn = document.getElementById("btn-sync");
syncBtn.addEventListener("click", () => setTimeout(() => drawer.classList.add("hidden"), 600));

updateMiniStats();

// Экран прогресса (в v1 данные были размазаны по сайдбару — тут отдельный таб).
function showProgress() {
  content.innerHTML = "";
  const c = el("div", { className: "card" });
  c.append(el("h1", {}, "📊 Прогресс"));
  c.append(el("p", { className: "dim" },
    `Уровень: ${st.level ?? "— (нужен тест)"} · карточек в SRS: ${Object.keys(st.cards).length}`));
  c.append(el("div", { className: "sec" }, "Минуты сегодня"));
  for (const [lang, name] of Object.entries(LANG_NAMES)) {
    const m = st.minutes[lang] || 0;
    c.append(el("div", { className: "dim" }, `${name}: ${m.toFixed(1)} мин`));
    const bar = el("div", { className: "bar" });
    bar.append(el("i", { style: `width:${Math.min(100, m / 20 * 100)}%;background:${LANG_COLORS[lang]}` }));
    c.append(bar);
  }
  const hist = st.history || [];
  c.append(el("div", { className: "sec" }, "Последние уроки"));
  if (!hist.length) c.append(el("p", { className: "dim" }, "пока пусто — начни урок"));
  for (const h of hist.slice(-8).reverse())
    c.append(el("div", { className: "kv" },
      el("b", {}, String(h.score)), el("span", { className: "dim" },
        `${h.lesson} · ${new Date(h.ts * 1000).toLocaleString()}`)));
  c.append(el("button", { className: "cta green block", onclick: startLesson }, "▶ Новый урок"));
  content.append(c);
  setNav("progress");
}

// --------------------------------------------------------------- шаг C ----
// Мобильные улучшения поверх готовых функций app.js (обёртки, не копии):

// 1) Крупные тач-цели уже в CSS; здесь — вибро-отклик на результат ответа
//    (где поддерживается), чтобы верный/неверный ощущались без взгляда.
let _gradeAnswerOrig;
_gradeAnswerOrig = gradeAnswer;
window.gradeAnswer = function (correct, isPlacement, clickedIdx) {
  if (navigator.vibrate) navigator.vibrate(correct >= 0.999 ? 15 : [40, 30, 40]);
  return _gradeAnswerOrig(correct, isPlacement, clickedIdx);
};

// 2) Клавиатура съедает полэкрана: при фокусе в поле ввода прокручиваем
//    задание так, чтобы оно осталось видимым над клавиатурой.
document.addEventListener("focusin", e => {
  if (e.target && e.target.id === "typein")
    setTimeout(() => e.target.scrollIntoView({ block: "center", behavior: "smooth" }), 250);
});

// 3) Защита от случайного закрытия урока: pull-to-refresh и двойной тап по
//    контенту ничего не делают; уход с незавершённого задания подтверждаем.
window.addEventListener("beforeunload", e => {
  if (sessionAnswers && sessionAnswers.length && taskI < tasks.length) {
    e.preventDefault(); e.returnValue = "";
  }
});

// 4) theme-color мета подстраиваем под тему (статус-бар Android сливается с UI).
let _setThemeOrig;
_setThemeOrig = setTheme;
window.setTheme = function (name) {
  _setThemeOrig(name);
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = getComputedStyle(document.body).getPropertyValue("--panel").trim() || "#141a26";
};

// 5) Явная маркировка трафика: сервер логирует fetch'и как [client=web-mobile]
//    (см. _client_kind в local_server/server.py), а не как обычный 'web'.
window.__TL_CLIENT__ = "web-mobile";

// Переключалка версий: ссылка на десктопную v1 (полезно на планшете/тестов).
(function addVersionSwitch() {
  const sheet = document.querySelector("#drawer .drawer-sheet");
  if (!sheet) return;
  sheet.append(el("a", { href: "/desktop", className: "ghost block", style: "text-align:center;text-decoration:none" },
    "🖥 Открыть десктопную версию"));
})();

console.info("[mobile] v2 loaded:", location.pathname, "| core:", typeof C);
