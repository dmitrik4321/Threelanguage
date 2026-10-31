# Пакет для PR: Python desktop test build (tkinter)

Цель: github.com/dmitrik4321/Threelanguage, base-ветка `main` (коммит `dfa015d`).
Head-ветка PR: `feature/python-desktop-build`. Никаких force-операций — только создание новой ветки.

## Состав изменений (5 файлов, +576/-1)
- `.gitignore` — убрана строка `build/` (иначе папка билда не попадает в публикацию)
- `build/app.py` — UI на tkinter поверх эталонного ядра shared/algorithm/reference.py:
  placement (адаптивный IRT) -> урок (SRS FSRS-lite, AFK-темп-монитор, скоринг 0..100),
  прогресс дня по трём языкам, состояние в SQLite ~/.threelanguage/desktop.db
- `build/content.py` — данные-заглушки (банк вопросов placement, карточки урока)
- `build/smoke_test.py` — автопрогон под Xvfb (12 проверок, SMOKE OK)
- `build/README.md` — как запустить и ограничения

## Вариант A — применить локально и запушить новую ветку (рекомендую)
```bash
cd Threelanguage
git checkout -b feature/python-desktop-build dfa015d
git am pr_package/0001-Python-desktop-test-build-tkinter-placement-lesson-A.patch
# (или: git apply pr_package/desktop_build.patch && git add -A && git commit -m "Python desktop test build")
git push origin feature/python-desktop-build     # обычный push, не force
```
Затем на GitHub: Compare & pull request → base `main`, compare `feature/python-desktop-build`.

## Вариант B — через веб-интерфейс GitHub (без git вообще)
На странице репо: `?expand=1`, создать файл/загрузить содержимое из `pr_package/build_snapshot.zip`
в новую ветку, затем открыть PR.

## Проверка перед отправкой
```bash
python3 build/smoke_test.py   # нужен Xvfb: xvfb-run python3 build/smoke_test.py
./tools_ci.sh                 # паритет Python/C++ ядра не затронут
```
