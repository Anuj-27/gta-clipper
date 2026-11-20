"""Reusable custom widgets: cards, live line graphs."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from ui import theme


class Card(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(16, 14, 16, 14)
        self.layout_.setSpacing(8)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("CardTitle")
            self.layout_.addWidget(t)

    def add(self, w: QWidget, stretch: int = 0) -> QWidget:
        self.layout_.addWidget(w, stretch)
        return w


class StatCard(Card):
    def __init__(self, title: str, value: str = "—", parent: QWidget | None = None):
        super().__init__(title, parent)
        self.value = QLabel(value)
        self.value.setObjectName("BigValue")
        self.value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.value.setWordWrap(True)
        self.add(self.value)

    def set(self, text: str, color: str | None = None) -> None:
        self.value.setText(text)
        self.value.setStyleSheet(f"color: {color};" if color else "")


class LineGraph(QWidget):
    """Lightweight scrolling/area line chart with optional event markers & threshold."""

    def __init__(self, color: str = theme.ACCENT, max_points: int = 180, parent: QWidget | None = None):
        super().__init__(parent)
        self.color = QColor(color)
        self.max_points = max_points
        self.xs: deque[float] = deque(maxlen=max_points)
        self.ys: deque[float] = deque(maxlen=max_points)
        self.markers: deque[float] = deque(maxlen=200)
        self.threshold: float | None = None
        self.fixed_max: float | None = None
        self.unit = ""
        self.setMinimumHeight(120)

    def clear(self) -> None:
        self.xs.clear()
        self.ys.clear()
        self.markers.clear()
        self.update()

    def add_point(self, x: float, y: float) -> None:
        self.xs.append(float(x))
        self.ys.append(float(y))
        self.update()

    def set_series(self, xs: list[float], ys: list[float]) -> None:
        self.xs = deque(xs, maxlen=max(len(xs), 1))
        self.ys = deque(ys, maxlen=max(len(ys), 1))
        self.update()

    def add_marker(self, x: float) -> None:
        self.markers.append(float(x))
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(8, 8, -8, -18)
        p.fillRect(self.rect(), QColor(theme.BG_3))
        grid = QPen(QColor(theme.BORDER), 1, Qt.DashLine)
        p.setPen(grid)
        for i in range(1, 4):
            y = r.top() + r.height() * i / 4
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        if len(self.xs) < 2:
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(self.rect(), Qt.AlignCenter, "Waiting for data…")
            return
        x0, x1 = self.xs[0], self.xs[-1]
        span = max(x1 - x0, 1e-6)
        ymax = self.fixed_max or max(max(self.ys), 1e-6) * 1.15

        def pt(x: float, y: float) -> QPointF:
            return QPointF(r.left() + (x - x0) / span * r.width(), r.bottom() - min(y, ymax) / ymax * r.height())

        path = QPainterPath(pt(self.xs[0], self.ys[0]))
        for x, y in zip(self.xs, self.ys):
            path.lineTo(pt(x, y))
        area = QPainterPath(path)
        area.lineTo(QPointF(r.right(), r.bottom()))
        area.lineTo(QPointF(r.left(), r.bottom()))
        area.closeSubpath()
        grad = QLinearGradient(0, r.top(), 0, r.bottom())
        c1 = QColor(self.color)
        c1.setAlpha(110)
        c2 = QColor(self.color)
        c2.setAlpha(5)
        grad.setColorAt(0, c1)
        grad.setColorAt(1, c2)
        p.fillPath(area, QBrush(grad))
        p.setPen(QPen(self.color, 2))
        p.drawPath(path)

        if self.threshold is not None and self.threshold < ymax:
            p.setPen(QPen(QColor(theme.YELLOW), 1, Qt.DotLine))
            y = pt(x0, self.threshold).y()
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))

        p.setPen(QPen(QColor(theme.GREEN), 2))
        for m in self.markers:
            if x0 <= m <= x1:
                x = pt(m, 0).x()
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

        p.setPen(QColor(theme.TEXT_DIM))
        f = p.font()
        f.setPointSize(8)
        p.setFont(f)
        p.drawText(QPointF(r.left(), self.height() - 4), _fmt_time(x0))
        p.drawText(QPointF(r.right() - 40, self.height() - 4), _fmt_time(x1))
        p.drawText(QPointF(r.right() - 60, r.top() + 10), f"{self.ys[-1]:.1f}{self.unit}")


def _fmt_time(s: float) -> str:
    s = int(max(s, 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


fmt_time = _fmt_time
