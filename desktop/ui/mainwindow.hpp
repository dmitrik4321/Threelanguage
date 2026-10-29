#pragma once
#ifdef TL_HAS_QT
#include <QMainWindow>
class MainWindow : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(QWidget* parent = nullptr);
protected:
    void paintEvent(QPaintEvent*) override;      // pixel_canvas: NEAREST-скейл маскота
private:
    int currentMoodFrame_ = 0;                    // idle|cheer|angry|sad|sleep <- mascot_state
};
#endif
