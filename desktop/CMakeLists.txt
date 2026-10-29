cmake_minimum_required(VERSION 3.22)
project(threelanguage-desktop LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Ядро — header-only, без Qt: тесты гоняются на любой машине
enable_testing()
add_executable(core_tests tests/core_tests.cpp)
target_include_directories(core_tests PRIVATE core)
add_test(NAME core_fixtures COMMAND core_tests ${CMAKE_SOURCE_DIR}/../shared/algorithm/fixtures)

# UI (Qt6) включается после ревью стека:
find_package(Qt6 COMPONENTS Widgets QUIET)
if(Qt6_FOUND)
  add_executable(threelanguage MACOSX_BUNDLE WIN32
    ui/main.cpp ui/mainwindow.cpp ui/pixel_canvas.cpp)
  target_link_libraries(threelanguage PRIVATE Qt6::Widgets)
  target_include_directories(threelanguage PRIVATE core)
endif()
