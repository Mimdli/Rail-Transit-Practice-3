"""联锁HMI线路图 — 面向信号演示的黑底双线示意图。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QBrush, QFont, QFontDatabase, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsLineItem,
    QGraphicsPathItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QLabel,
    QVBoxLayout,
    QWidget,
)


@dataclass(frozen=True)
class _TrackPoint:
    """HMI图上的线路坐标点。"""

    x: float
    y: float


class InterlockingSceneView(QGraphicsView):
    """按信号联锁屏风格绘制线路、站台、信号机和道岔。"""

    BG = QColor("#020403")
    TRACK = QColor("#4bd7e5")
    TRACK_DIM = QColor("#1c7781")
    TEXT = QColor("#d9fff0")
    GREEN = QColor("#00e83a")
    YELLOW = QColor("#ffff00")
    RED = QColor("#ff2222")
    GREY = QColor("#8e8e8e")
    PLATFORM = QColor("#9dd7a3")
    TRAIN = QColor("#ffb000")

    def __init__(self, track_data=None, parent=None):
        super().__init__(parent)
        self._ensure_fonts()
        self.track_data = track_data
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setBackgroundBrush(QBrush(self.BG))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self._train_items: list[QGraphicsRectItem] = []
        self._dynamic_items: list = []
        self._segment_items: dict[int, list[QGraphicsLineItem]] = {}
        self._signal_lamps: dict[str, QGraphicsEllipseItem] = {}
        self._total_length = 1.0
        self._left = 80.0
        self._right = 1200.0
        self._up_y = 230.0
        self._down_y = 360.0
        self._auto_fit_done = False
        self.rebuild()

    @staticmethod
    def _ensure_fonts():
        """离屏预览时 Qt 可能没有系统字体，显式加载 Windows 常用字体。"""
        for path in (
            r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            r"C:\Windows\Fonts\arial.ttf",
        ):
            QFontDatabase.addApplicationFont(path)

    def set_track_data(self, track_data):
        self.track_data = track_data
        self.rebuild()

    def wheelEvent(self, event):  # noqa: N802 - Qt API
        self._auto_fit_done = True
        factor = 1.18 if event.angleDelta().y() > 0 else 1 / 1.18
        self.scale(factor, factor)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        if not self._auto_fit_done and self._scene.items():
            self._fit_to_height()

    def rebuild(self):
        """重建整张HMI图，所有元素来自当前TrackData。"""
        self._scene.clear()
        self._train_items.clear()
        self._dynamic_items.clear()
        self._segment_items.clear()
        self._signal_lamps.clear()
        self._auto_fit_done = False
        self._scene.setBackgroundBrush(QBrush(self.BG))

        if not self.track_data:
            self._scene.setSceneRect(QRectF(0, 0, 1200, 700))
            self._add_text("暂无线路数据", 80, 80, self.TEXT, 18)
            return

        self._total_length = max(1.0, float(self.track_data.total_length()))
        station_count = max(2, len(self.track_data.stations))
        self._left = 100.0
        self._right = max(1380.0, self._left + (station_count - 1) * 280.0)
        self._up_y = 230.0
        self._down_y = 350.0
        width = self._right + 140.0
        height = 560.0
        self._scene.setSceneRect(QRectF(0, 0, width, height))

        self._draw_frame(width)
        self._draw_main_tracks()
        self._draw_stations()
        self._draw_platforms()
        self._draw_sections()
        self._draw_turnouts()
        self._draw_signals()
        self._draw_legend(width)

        self._fit_to_height()

    def set_train_position(self, car_abs_positions: Iterable[float], controller=None):
        """在HMI图上用橙色小车块展示当前列车占用。"""
        for item in self._train_items:
            self._scene.removeItem(item)
        self._train_items.clear()

        positions = list(car_abs_positions or [])
        if not positions:
            return
        for idx, abs_pos in enumerate(positions[:8]):
            point = self._main_point(float(abs_pos), prefer_up=False)
            rect = QGraphicsRectItem(point.x - 12, point.y - 9 - idx * 0.4, 24, 18)
            rect.setBrush(QBrush(self.TRAIN))
            rect.setPen(QPen(QColor("#ffe18a"), 1.2))
            rect.setZValue(30)
            self._scene.addItem(rect)
            self._train_items.append(rect)

    def set_dispatch_state(self, runtimes: Iterable, signal_system,
                           occupancy: dict[int, frozenset[str]],
                           locks: dict[int, str]):
        """刷新联锁屏的列车、区段占用、进路锁闭和真实信号显示。"""
        for item in self._train_items:
            self._scene.removeItem(item)
        self._train_items.clear()
        for item in self._dynamic_items:
            self._scene.removeItem(item)
        self._dynamic_items.clear()

        for segment_id, items in self._segment_items.items():
            if segment_id in occupancy:
                color, width = self.RED, 6.0
            elif segment_id in locks:
                color, width = self.YELLOW, 5.0
            else:
                color, width = self.TRACK, 3.2
            for item in items:
                item.setPen(QPen(color, width, Qt.SolidLine, Qt.RoundCap))

        if len(self.track_data.segments) <= 30:
            active_segments = sorted(set(occupancy) | set(locks))
            last_label_x = -9999.0
            for segment_id in active_segments[:16]:
                segment = self.track_data._seg_map.get(segment_id)
                if segment is None:
                    continue
                x = self._x_for(segment.abs_start + segment.length / 2.0)
                if x - last_label_x < 72:
                    continue
                state = "占用" if segment_id in occupancy else "锁闭"
                color = self.RED if state == "占用" else self.YELLOW
                label = self._add_text(
                    f"LK{segment_id} {state}", x,
                    (self._up_y + self._down_y) / 2 - 8,
                    color, 7, True, True)
                self._dynamic_items.append(label)
                last_label_x = x

        for signal in self.track_data.signals:
            lamp = self._signal_lamps.get(signal.signal_id)
            if lamp is None:
                continue
            aspect = signal_system.get_signal_aspect(signal).value
            color = {"红灯": self.RED, "黄灯": self.YELLOW,
                     "绿灯": self.GREEN}.get(aspect, self.GREY)
            lamp.setBrush(QBrush(color))

        lanes: dict[tuple[int, int], int] = {}
        for runtime in tuple(runtimes):
            direction = runtime.controller.direction
            point = self._main_point(runtime.head_abs, prefer_up=direction < 0)
            slot_key = (direction, round(point.x / 80))
            slot = lanes.get(slot_key, 0)
            lanes[slot_key] = slot + 1
            y = point.y + (-54 - slot * 24 if direction < 0 else 42 + slot * 24)
            rect = QGraphicsRectItem(point.x - 20, y, 40, 16)
            rect.setBrush(QBrush(self.TRAIN))
            rect.setPen(QPen(QColor("#ffe18a"), 1.2))
            rect.setZValue(35)
            self._scene.addItem(rect)
            label = self._add_text(
                f"{runtime.train_id} {runtime.speed_kmh:.0f}", point.x,
                y - 21 if direction < 0 else y + 19,
                self.TEXT, 8, True, True)
            self._dynamic_items.extend((rect, label))

    def _x_for(self, abs_pos: float) -> float:
        ratio = min(1.0, max(0.0, abs_pos / self._total_length))
        return self._left + ratio * (self._right - self._left)

    def _fit_to_height(self):
        """联锁屏通常横向滚动，默认按高度适配以保证文字可读。"""
        rect = self._scene.sceneRect()
        if rect.height() <= 0:
            return
        self.resetTransform()
        view_h = max(1, self.viewport().height())
        scale = max(0.78, view_h / rect.height() * 0.92)
        self.scale(scale, scale)
        self.centerOn(min(self._left + 520, rect.right()), rect.center().y())

    def _main_point(self, abs_pos: float, prefer_up: bool) -> _TrackPoint:
        return _TrackPoint(self._x_for(abs_pos), self._up_y if prefer_up else self._down_y)

    def _add_text(self, text: str, x: float, y: float, color: QColor, size: int = 10,
                  bold: bool = False, center: bool = False) -> QGraphicsPathItem:
        font = QFont("Microsoft YaHei UI", size)
        font.setBold(bold)
        path = QPainterPath()
        path.addText(0, 0, font, str(text))
        item = QGraphicsPathItem(path)
        item.setBrush(QBrush(color))
        item.setPen(QPen(color, 0.1))
        item.setZValue(40)
        self._scene.addItem(item)
        if center:
            rect = item.boundingRect()
            item.setPos(x - rect.width() / 2, y + size)
        else:
            item.setPos(x, y + size)
        return item

    def _draw_frame(self, width: float):
        pen = QPen(QColor("#474747"), 1.0)
        self._scene.addLine(40, 46, width - 70, 46, pen)
        self._scene.addLine(40, 500, width - 70, 500, pen)
        self._add_text(f"{self._total_length / 1000:.3f}Km", 42, 24, self.TEXT, 9)
        self._add_text("联锁HMI · 实时占用与进路", 70, 512, self.YELLOW, 15, True)

    def _draw_main_tracks(self):
        dim_pen = QPen(self.TRACK_DIM, 1.0)
        self._scene.addLine(self._left, self._up_y, self._right, self._up_y, dim_pen)
        self._scene.addLine(self._left, self._down_y, self._right, self._down_y, dim_pen)

        for x in self._tick_positions():
            self._scene.addLine(x, self._up_y - 8, x, self._up_y + 8, QPen(QColor("#b8d6dc"), 1.0))
            self._scene.addLine(x, self._down_y - 8, x, self._down_y + 8, QPen(QColor("#b8d6dc"), 1.0))

    def _draw_stations(self):
        stations = sorted(self.track_data.stations, key=lambda s: (s.position, s.station_id))
        if not stations:
            return
        for idx, station in enumerate(stations, start=1):
            x = self._x_for(station.position)
            self._scene.addLine(x, self._up_y - 18, x, self._down_y + 18,
                                QPen(QColor("#52646a"), 1.0, Qt.DashLine))
            title = station.name or f"站{idx}"
            self._add_text(title, x, 104, self.TEXT, 13, True, True)
            self._add_text(f"站{idx}", x, 134, self.GREY, 8, center=True)

    def _draw_platforms(self):
        platforms = sorted(self.track_data.platforms, key=lambda p: (p.position, p.platform_id))
        for platform in platforms:
            x = self._x_for(platform.position)
            y = self._up_y - 32 if platform.direction == "up" else self._down_y + 20
            rect = QGraphicsRectItem(x - 34, y, 68, 9)
            rect.setBrush(QBrush(self.PLATFORM))
            rect.setPen(QPen(self.GREEN, 2.0))
            self._scene.addItem(rect)

    def _draw_sections(self):
        segments = sorted(self.track_data.segments, key=lambda s: (s.abs_start, s.seg_id))
        if not segments:
            return
        for seg in segments:
            if not (self._valid_neighbor(seg.start_neighbor)
                    or self._valid_neighbor(seg.end_neighbor)):
                continue
            x1 = self._x_for(seg.abs_start)
            x2 = self._x_for(seg.abs_start + seg.length)
            items = []
            for y in (self._up_y, self._down_y):
                item = self._scene.addLine(
                    x1, y, x2, y,
                    QPen(self.TRACK, 3.2, Qt.SolidLine, Qt.RoundCap))
                item.setZValue(5)
                items.append(item)
            self._segment_items[seg.seg_id] = items
            mid = seg.abs_start + seg.length / 2
            x = self._x_for(mid)
            if len(segments) <= 30:
                self._add_text(
                    f"LK{seg.seg_id}", x,
                    (self._up_y + self._down_y) / 2 - 8,
                    self.GREY, 7, center=True)

    def _draw_turnouts(self):
        raw_turnouts = [
            (s, s.abs_start if self._valid_neighbor(s.start_lateral) else s.abs_start + s.length)
            for s in self.track_data.segments
            if self._valid_neighbor(s.start_lateral) or self._valid_neighbor(s.end_lateral)
        ]
        turnout_segments = self._cluster_turnouts(raw_turnouts)
        pen = QPen(self.TRACK, 2.4)
        marker_pen = QPen(self.RED, 1.2)
        for idx, (_, base) in enumerate(turnout_segments[:10]):
            x = self._x_for(base)
            span = 58 + (idx % 3) * 18
            if idx % 4 in (0, 1):
                self._scene.addLine(x - span, self._up_y, x, self._down_y, pen)
                self._scene.addLine(x, self._down_y, x + span, self._up_y, pen)
            else:
                y = self._up_y if idx % 2 else self._down_y
                side = -1 if idx % 2 else 1
                self._scene.addLine(x, y, x + side * span, y + side * 58, pen)
                self._scene.addLine(x + side * span, y + side * 58, x + side * (span + 54), y + side * 58, pen)

            dot = QGraphicsEllipseItem(x - 4, (self._up_y + self._down_y) / 2 - 4, 8, 8)
            dot.setBrush(QBrush(self.RED))
            dot.setPen(marker_pen)
            self._scene.addItem(dot)

    def _draw_signals(self):
        signals = sorted(self.track_data.signals, key=lambda s: (s.position, s.signal_id))
        if not signals:
            return
        last_x = -9999.0
        min_gap = max(125.0, (self._right - self._left) / 25.0)
        drawn = 0
        for idx, sig in enumerate(signals):
            x = self._x_for(sig.position)
            if x - last_x < min_gap:
                continue
            y = self._up_y - 40 if idx % 2 == 0 else self._down_y + 38
            color = self.GREY
            self._draw_signal(sig.signal_id, x, y, color, above=idx % 2 == 0)
            last_x = x
            drawn += 1
            if drawn >= 36:
                break

    def _draw_signal(self, signal_id: str, x: float, y: float, color: QColor, above: bool):
        stem_y = self._up_y if above else self._down_y
        self._scene.addLine(x, stem_y, x, y, QPen(QColor("#e0e0e0"), 1.0))
        lamp = QGraphicsEllipseItem(x - 7, y - 7, 14, 14)
        lamp.setBrush(QBrush(color))
        lamp.setPen(QPen(QColor("#202020"), 1.2))
        self._scene.addItem(lamp)
        self._signal_lamps[signal_id] = lamp
        if signal_id and len(signal_id) <= 4:
            self._add_text(signal_id, x + 9, y - 8, color, 6)

    def _draw_legend(self, width: float):
        x = width - 420
        y = 72
        entries = [("空闲", self.TRACK), ("占用", self.RED),
                   ("锁闭", self.YELLOW), ("列车", self.TRAIN)]
        for idx, (label, color) in enumerate(entries):
            cx = x + idx * 95
            lamp = QGraphicsEllipseItem(cx, y, 15, 15)
            lamp.setBrush(QBrush(color))
            lamp.setPen(QPen(QColor("#333333"), 1.0))
            self._scene.addItem(lamp)
            self._add_text(label, cx - 12, y + 20, self.TEXT, 8)

    def _tick_positions(self) -> list[float]:
        count = max(8, min(42, int(self._total_length // 160) or 8))
        return [self._left + i * (self._right - self._left) / count for i in range(count + 1)]

    def _cluster_turnouts(self, raw_turnouts):
        """把同一站场密集道岔合并成少量可读的联锁屏符号。"""
        clustered = []
        last_x = -9999.0
        for seg, base in sorted(raw_turnouts, key=lambda item: (item[1], item[0].seg_id)):
            x = self._x_for(base)
            if x - last_x < 175.0:
                continue
            clustered.append((seg, base))
            last_x = x
        return clustered

    @staticmethod
    def _valid_neighbor(seg_id: int) -> bool:
        return seg_id > 0 and seg_id != 65535


class InterlockingViewWidget(QWidget):
    """联锁HMI图页签容器。"""

    def __init__(self, track_data=None, parent=None):
        super().__init__(parent)
        self.track_data = track_data
        self.status = QLabel()
        self.status.setObjectName("interlockingStatus")
        self.status.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.status.setFixedHeight(30)
        self.status.setStyleSheet(
            "#interlockingStatus {"
            "background: #0b1115;"
            "color: #c9f8ee;"
            "padding: 0 12px;"
            "font: 12px 'Microsoft YaHei UI';"
            "border-bottom: 1px solid #1d2b31;"
            "}"
        )
        self.view = InterlockingSceneView(track_data)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.status)
        layout.addWidget(self.view, stretch=1)
        self._refresh_status()

    def set_track_data(self, track_data, source_name: str = ""):
        self.track_data = track_data
        self.view.set_track_data(track_data)
        self._refresh_status(source_name)

    def set_train_position(self, car_abs_positions: Iterable[float], controller=None):
        self.view.set_train_position(car_abs_positions, controller)

    def set_dispatch_state(self, runtimes: Iterable, signal_system,
                           occupancy: dict[int, frozenset[str]],
                           locks: dict[int, str]):
        runtimes = tuple(runtimes)
        self.view.set_dispatch_state(
            runtimes, signal_system, occupancy, locks)
        self.status.setText(
            f"联锁HMI | 列车 {len(runtimes)} | 占用 {len(occupancy)} | "
            f"锁闭 {len(locks)} | 信号随闭塞状态实时刷新"
        )

    def _refresh_status(self, source_name: str = ""):
        if not self.track_data:
            self.status.setText("联锁HMI图 | 暂无线路数据")
            return
        prefix = f"{source_name} | " if source_name else ""
        self.status.setText(
            f"联锁HMI图 | {prefix}"
            f"区段 {len(self.track_data.segments)} | "
            f"车站 {len(self.track_data.stations)} | "
            f"站台 {len(self.track_data.platforms)} | "
            f"信号 {len(self.track_data.signals)} | "
            f"总长 {self.track_data.total_length():.0f} m"
        )
