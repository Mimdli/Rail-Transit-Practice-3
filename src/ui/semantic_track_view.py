"""运营语义线路图 — 面向运行仿真的平面线路可视化。"""

from __future__ import annotations

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

from src.track.semantic_line import SemanticLineModel, build_semantic_line


class SemanticTrackSceneView(QGraphicsView):
    """绘制“车站-区间-辅助线”层级，而不是直接绘制全部 Seg。"""

    BG = QColor("#f7fafc")
    LINE = QColor("#2457c5")
    LINE_SOFT = QColor("#9cb8ed")
    STATION_FILL = QColor("#ffffff")
    STATION_STROKE = QColor("#172554")
    TEXT = QColor("#172033")
    MUTED = QColor("#64748b")
    BRANCH = QColor("#94a3b8")
    TRAIN = QColor("#f59e0b")
    DOWN_LINE = QColor("#2457c5")
    UP_LINE = QColor("#0f766e")

    def __init__(self, track_data=None, parent=None):
        super().__init__(parent)
        self._ensure_fonts()
        self.track_data = track_data
        self.model: SemanticLineModel | None = None
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setBackgroundBrush(QBrush(self.BG))
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._station_x: dict[int, float] = {}
        self._train_items: list[QGraphicsRectItem] = []
        self._dynamic_items: list = []
        self._link_items: list[tuple] = []
        self._left = 90.0
        self._top = 120.0
        self._station_gap = 190.0
        self._down_y = 230.0
        self._up_y = 320.0
        self.rebuild()

    @staticmethod
    def _ensure_fonts():
        """保证离屏预览和部分 Windows 环境下中文字体可用。"""
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
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)

    def rebuild(self):
        """根据语义模型重建整张运营线路图。"""
        self._scene.clear()
        self._train_items.clear()
        self._dynamic_items.clear()
        self._link_items.clear()
        self._station_x.clear()

        if not self.track_data:
            self._scene.setSceneRect(QRectF(0, 0, 1000, 520))
            self._add_text("暂无线路数据", 80, 80, 16, self.TEXT, True)
            return

        self.model = build_semantic_line(self.track_data)
        station_count = max(2, len(self.model.stations))
        width = self._left * 2 + (station_count - 1) * self._station_gap
        height = 560.0
        self._scene.setSceneRect(QRectF(0, 0, width + 280, height))

        self._draw_title()
        self._layout_stations()
        self._draw_main_line()
        self._draw_branches()
        self._draw_stations()
        self._draw_summary(width)
        self.resetTransform()
        self.centerOn(self._left + 420, (self._down_y + self._up_y) / 2)

    def set_train_position(self, car_abs_positions: Iterable[float], controller=None):
        """按语义里程投影列车位置，避免被底层 Seg 分支影响。"""
        for item in self._train_items:
            self._scene.removeItem(item)
        self._train_items.clear()

        for idx, abs_pos in enumerate(list(car_abs_positions or [])[:8]):
            x = self._x_for_position(float(abs_pos))
            y = self._down_y if idx < 4 else self._up_y
            rect = QGraphicsRectItem(x - 14, y - 23 - idx * 0.2, 28, 14)
            rect.setBrush(QBrush(self.TRAIN))
            rect.setPen(QPen(QColor("#92400e"), 1.0))
            rect.setZValue(30)
            self._scene.addItem(rect)
            self._train_items.append(rect)

    def set_dispatch_state(self, runtimes: Iterable,
                           occupancy: dict[int, frozenset[str]]):
        """动态刷新多列车位置和运营区间占用状态。"""
        for item in self._train_items:
            self._scene.removeItem(item)
        self._train_items.clear()
        for item in self._dynamic_items:
            self._scene.removeItem(item)
        self._dynamic_items.clear()

        occupied_ids = set(occupancy)
        for link, down_item, up_item in self._link_items:
            occupied = bool(occupied_ids.intersection(link.seg_ids))
            down_item.setPen(QPen(
                QColor("#dc2626") if occupied else self.DOWN_LINE,
                7.0 if occupied else 5.0, Qt.SolidLine, Qt.RoundCap))
            up_item.setPen(QPen(
                QColor("#dc2626") if occupied else self.UP_LINE,
                7.0 if occupied else 5.0, Qt.SolidLine, Qt.RoundCap))

        lanes: dict[tuple[int, int], int] = {}
        palette = ("#f59e0b", "#7c3aed", "#0891b2", "#2563eb", "#0f766e")
        for index, runtime in enumerate(tuple(runtimes)):
            x = self._x_for_position(runtime.head_abs)
            direction = runtime.controller.direction
            base_y = self._down_y if direction > 0 else self._up_y
            slot_key = (direction, round(x / 70))
            slot = lanes.get(slot_key, 0)
            lanes[slot_key] = slot + 1
            y = base_y + (-30 - slot * 24 if direction > 0 else 18 + slot * 24)
            color = QColor(palette[index % len(palette)])
            marker = QGraphicsRectItem(x - 18, y, 36, 16)
            marker.setBrush(QBrush(color))
            marker.setPen(QPen(QColor("#ffffff"), 1.5))
            marker.setZValue(35)
            self._scene.addItem(marker)
            label = self._add_text(
                f"{runtime.train_id}  {runtime.speed_kmh:.0f}km/h",
                x, y - 21 if direction > 0 else y + 18,
                8, color, True, center=True)
            self._dynamic_items.extend((marker, label))

    def _layout_stations(self):
        if not self.model:
            return
        for index, station in enumerate(self.model.stations):
            self._station_x[station.station_id] = self._left + index * self._station_gap

    def _draw_title(self):
        self._add_text("运营线路图", 72, 46, 22, self.TEXT, True)
        self._add_text("按车站和运营区间抽象展示，工程 Seg 信息收敛到详情层", 72, 78, 10, self.MUTED)

    def _draw_main_line(self):
        if not self.model or len(self.model.stations) < 2:
            return
        first = self._station_x[self.model.stations[0].station_id]
        last = self._station_x[self.model.stations[-1].station_id]

        # 上下行分开绘制，避免看起来只有一个方向。
        self._draw_direction_line(first, last, self._down_y, self.DOWN_LINE, "下行", "→")
        self._draw_direction_line(first, last, self._up_y, self.UP_LINE, "上行", "←")

        for link in self.model.links:
            start_x = self._station_x.get(link.start_station_id)
            end_x = self._station_x.get(link.end_station_id)
            if start_x is None or end_x is None:
                continue
            mid = (start_x + end_x) / 2
            distance = abs(link.end_pos - link.start_pos)
            label = f"{distance / 1000:.2f} km" if distance > 1.0 else "里程未标定"
            self._add_text(label, mid, (self._down_y + self._up_y) / 2 - 6, 8, self.MUTED, center=True)
            down_item = self._scene.addLine(
                start_x, self._down_y, end_x, self._down_y,
                QPen(self.DOWN_LINE, 5.0, Qt.SolidLine, Qt.RoundCap))
            up_item = self._scene.addLine(
                start_x, self._up_y, end_x, self._up_y,
                QPen(self.UP_LINE, 5.0, Qt.SolidLine, Qt.RoundCap))
            down_item.setZValue(3)
            up_item.setZValue(3)
            self._link_items.append((link, down_item, up_item))

    def _draw_direction_line(self, first: float, last: float, y: float, color: QColor,
                             name: str, arrow: str):
        item = QGraphicsLineItem(first, y, last, y)
        item.setPen(QPen(color, 5.0, Qt.SolidLine, Qt.RoundCap))
        item.setZValue(2)
        self._scene.addItem(item)
        self._add_text(f"{name} {arrow}", first - 64, y - 9, 10, color, True)

        step = 520.0
        x = first + 260.0
        while x < last - 120:
            self._add_text(arrow, x, y - 12, 12, color, True, center=True)
            x += step

    def _draw_branches(self):
        if not self.model:
            return
        branch_pen = QPen(self.BRANCH, 3.0, Qt.SolidLine, Qt.RoundCap)
        seen_slots: dict[int, int] = {}
        for branch in self.model.branches[:28]:
            x = self._x_for_position(branch.anchor_pos)
            slot = seen_slots.get(round(x / 120), 0)
            seen_slots[round(x / 120)] = slot + 1
            direction = -1 if slot % 2 == 0 else 1
            base_y = self._down_y if direction < 0 else self._up_y
            stub_y = base_y + direction * (54 + min(slot, 2) * 24)
            self._scene.addLine(x, base_y, x + 46, stub_y, branch_pen)
            self._scene.addLine(x + 46, stub_y, x + 98, stub_y, branch_pen)
            self._add_text(branch.role, x + 104, stub_y - 7, 8, self.MUTED)

    def _draw_stations(self):
        if not self.model:
            return
        top_label_y = self._down_y - 120
        bottom_label_y = self._up_y + 58
        for index, station in enumerate(self.model.stations):
            x = self._station_x[station.station_id]
            self._scene.addLine(x, self._down_y, x, self._up_y, QPen(self.LINE_SOFT, 1.2, Qt.DashLine))
            for y, color in ((self._down_y, self.DOWN_LINE), (self._up_y, self.UP_LINE)):
                station_item = QGraphicsEllipseItem(x - 12, y - 12, 24, 24)
                station_item.setBrush(QBrush(self.STATION_FILL))
                station_item.setPen(QPen(color, 3.5))
                station_item.setZValue(10)
                self._scene.addItem(station_item)

            label_y = top_label_y if index % 2 == 0 else bottom_label_y
            anchor_y = self._down_y - 15 if index % 2 == 0 else self._up_y + 15
            self._scene.addLine(x, anchor_y, x, label_y + 18, QPen(self.LINE_SOFT, 1.1, Qt.DashLine))
            self._add_text(station.name, x, label_y, 13, self.TEXT, True, center=True)
            platform_label = f"站台 {len(station.platform_ids)}" if station.platform_ids else "站台 --"
            self._add_text(platform_label, x, label_y + 24, 8, self.MUTED, center=True)

    def _draw_summary(self, width: float):
        if not self.model:
            return
        text = (
            f"车站 {len(self.model.stations)}  |  "
            f"运营区间 {len(self.model.links)}  |  "
            f"辅助线组 {len(self.model.branches)}  |  "
            f"底层 Seg {len(self.track_data.segments)}"
        )
        self._add_text(text, 72, 494, 10, self.MUTED)
        self._scene.addLine(72, 476, width + 70, 476, QPen(QColor("#d4dce8"), 1.0))

    def _x_for_position(self, position: float) -> float:
        if not self.model or not self.model.stations:
            return self._left
        stations = self.model.stations
        if position <= stations[0].position:
            return self._station_x[stations[0].station_id]
        if position >= stations[-1].position:
            return self._station_x[stations[-1].station_id]

        for left, right in zip(stations, stations[1:]):
            if left.position <= position <= right.position:
                start_x = self._station_x[left.station_id]
                end_x = self._station_x[right.station_id]
                span = max(1.0, right.position - left.position)
                ratio = (position - left.position) / span
                return start_x + ratio * (end_x - start_x)
        return self._left

    def _add_text(self, text: str, x: float, y: float, size: int, color: QColor,
                  bold: bool = False, center: bool = False):
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
        return item


class SemanticTrackViewWidget(QWidget):
    """运营线路图页签容器。"""

    def __init__(self, track_data=None, parent=None):
        super().__init__(parent)
        SemanticTrackSceneView._ensure_fonts()
        self.track_data = track_data
        self.status = QLabel()
        self.status.setObjectName("semanticTrackStatus")
        self.status.setFixedHeight(32)
        self.status.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.status.setStyleSheet(
            "#semanticTrackStatus {"
            "background: #f8fafc;"
            "color: #334155;"
            "padding: 0 14px;"
            "font: 12px 'Microsoft YaHei UI';"
            "border-bottom: 1px solid #dbe3ee;"
            "}"
        )
        self.view = SemanticTrackSceneView(track_data)

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

    def set_dispatch_state(self, runtimes: Iterable,
                           occupancy: dict[int, frozenset[str]]):
        runtimes = tuple(runtimes)
        self.view.set_dispatch_state(runtimes, occupancy)
        occupied = len(occupancy)
        self.status.setText(
            f"运营线路图 | 实时列车 {len(runtimes)} | 占用区段 {occupied} | "
            "红色区间表示当前有列车占用"
        )

    def _refresh_status(self, source_name: str = ""):
        if not self.track_data:
            self.status.setText("运营线路图 | 暂无线路数据")
            return
        prefix = f"{source_name} | " if source_name else ""
        self.status.setText(
            f"运营线路图 | {prefix}"
            f"车站 {len(self.track_data.stations)} | "
            f"区段 {len(self.track_data.segments)} | "
            f"总长 {self.track_data.total_length():.0f} m"
        )
