#pragma once
// Рендер пиксель-арт ассетов без сглаживания: спрайты assets/mascot/*.png грузятся
// один раз, рисуется увеличенной копией с FastTransformation(NEAREST) и целым scale.
#ifdef TL_HAS_QT
#include <QPixmap>
#include <QPainter>
inline void draw_pixel_pixmap(QPainter& p, const QPixmap& src, QRectF target) {
    qreal s = std::max<qreal>(1.0, std::floor(target.width() / src.width()));
    QSize scaled(src.width() * (int)s, src.height() * (int)s);
    QPixmap smooth = src.scaled(scaled, Qt::KeepAspectRatio, Qt::FastTransformation);
    p.drawPixmap(target.topLeft().toPoint(), smooth);
}
#endif
