#!/usr/bin/env bash
# Единый прогон паритета ядра: Python-эталон -> C++ golden -> (Kotlin через Gradle, см. android/)
set -e
python3 shared/algorithm/run_fixtures.py
g++ -std=c++20 -O2 -Wall -o /tmp/tl_core_tests desktop/tests/core_tests.cpp
/tmp/tl_core_tests shared/algorithm/fixtures
echo "CORE PARITY OK"
