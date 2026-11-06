#!/usr/bin/env python3
"""Регресс-тесты «пустого урока» на мобилке.

Баг: веб-клиент просил /api/content/lesson?langs=en,pt-BR,zh; сервер резал
список по ','. При любой поломке кода ('pt'+','+'BR') живой конвейер молча
возвращал пустые пары, клиент получал lesson без tasks — и после закрытия
экрана загрузки оставался пустой экран (жалоба: «урок формируется, окно
закрывается, а урока нет»).

Проверяем офлайн (сеть не нужна):
  * _split_langs: ';' канон, ',' обратно совместим, склейка pt+BR, дефолт на мусоре;
  * #toast живёт на <body>, а не внутри #content (не стирается сменой экрана);
  * app.js: startLesson проверяет непустоту tasks и вызывает showTask ВНУТРИ
    try — hideLoading() в finally гарантированно идёт ПОСЛЕ отрисовки задания.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server  # noqa: E402


def main() -> int:
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("PASS" if cond else "FAIL"), name)
        if not cond:
            fails += 1

    # --- 1) разбор langs ---
    d = ["en", "pt-BR", "zh"]
    check("split: канонический ';'", server._split_langs("en;pt-BR;zh", d) == d)
    check("split: запятая с целыми кодами", server._split_langs("en,pt-BR,zh", d) == d)
    check("split: сломанное pt,BR склеивается",
          server._split_langs("en,pt,BR,zh", d) == ["en", "pt-BR", "zh"])
    check("split: мусор -> дефолт", server._split_langs("xx,yy", d) == d)
    check("split: пусто -> дефолт", server._split_langs("", d) == d)
    check("split: один язык остаётся одним",
          server._split_langs("pt-BR", d) == ["pt-BR"])

    # --- 2) статика клиента: toast вне #content (иначе смена экрана его стирает) ---
    webapp = Path(__file__).resolve().parents[1] / "webapp"
    for page in ("index.html", "mobile.html"):
        html = (webapp / page).read_text(encoding="utf-8")
        i_content = html.index("</main>")           # конец контейнера #content
        i_toast = html.index('id="toast"')
        check(f"{page}: #toast НЕ внутри #content", i_toast > i_content)

    # --- 3) app.js: защита startLesson от пустого/битого ответа ---
    js = (webapp / "static" / "app.js").read_text(encoding="utf-8")
    check("app.js: LANGS_QS с ';' разделителем", 'LANGS_QS = "en;pt-BR;zh"' in js)
    check("app.js: проверка пустых tasks перед showTask",
          'if (!ts || !ts.length)' in js)
    loading_marker = "// ---------------------------------------------------------------- loading ----"
    i_start = js.index("async function startLesson")
    i_end = js.index("async function startPlacement")
    body = js[i_start:i_end]
    check("app.js: showTask ВНУТРИ try (до finally hideLoading)",
          "try {" in body and body.index("showTask();") < body.index("finally"))

    print("LESSON FAILS:", fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
