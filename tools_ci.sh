#!/usr/bin/env bash
# Единый прогон паритета ядра: Python-эталон -> C++ golden -> (Kotlin через Gradle, см. android/)
set -e
python3 shared/algorithm/run_fixtures.py
g++ -std=c++20 -O2 -Wall -o /tmp/tl_core_tests desktop/tests/core_tests.cpp
/tmp/tl_core_tests shared/algorithm/fixtures
echo "CORE PARITY OK"

# Web-паритет: JS-порт ядра (web_core.js) против того же Python-эталона.
# node опционален (на машинах без node — пропуск, а не падение CI).
if command -v node >/dev/null 2>&1; then
    python3 local_server/tests/test_web_parity.py
    echo "WEB PARITY OK"
else
    echo "WEB PARITY SKIPPED (node not found)"
fi
