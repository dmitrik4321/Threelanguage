// Точка входа Desktop-клиента (Qt6). Включается в сборку, когда найден Qt6.
// MVP-экраны: Home (дракон + 3 прогресс-бара), Lesson, Micro-duel, Stats, Settings, Placement.
#include <QApplication>
#ifdef TL_HAS_QT
#include "mainwindow.hpp"
#endif

int main(int argc, char** argv) {
#ifdef TL_HAS_QT
    QApplication app(argc, argv);
    app.setApplicationName("Threelanguage");
    MainWindow w;
    w.show();
    return app.exec();
#else
    return 0; // core-only сборка (CI без Qt)
#endif
}
