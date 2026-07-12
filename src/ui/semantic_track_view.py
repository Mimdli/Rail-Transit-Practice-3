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
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.track.semantic_line import SemanticLineModel, build_semantic_line
from src.track.ats_layout import load_ats_layout
from src.track.link_mainline import LinkCoordinateMapper, load_mainline_links
from src.common.track_position import TrackPosition


class _ImmediateTooltipMixin:
    """绕过系统 Tooltip 延迟，在进入图元时立即显示信息。"""

    def set_immediate_tooltip(self, text: str):
        self._immediate_tooltip = text
        self.setToolTip(text)
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event):
        QToolTip.showText(event.screenPos(), self._immediate_tooltip)
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event):
        QToolTip.showText(event.screenPos(), self._immediate_tooltip)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):
        QToolTip.hideText()
        super().hoverLeaveEvent(event)


class ImmediateTooltipLineItem(_ImmediateTooltipMixin, QGraphicsLineItem):
    pass


class ImmediateTooltipPathItem(_ImmediateTooltipMixin, QGraphicsPathItem):
    pass


class ImmediateTooltipEllipseItem(_ImmediateTooltipMixin, QGraphicsEllipseItem):
    pass


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

    def __init__(self, track_data=None, parent=None,
                 show_details: bool = False):
        super().__init__(parent)
        self._ensure_fonts()
        self.track_data = track_data
        self.show_details = show_details
        self.model: SemanticLineModel | None = None
        self._link_mapper: LinkCoordinateMapper | None = None
        self._coord_points: list[float] = []
        self._coord_x: list[float] = []
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
        self._link_label_items: list = []
        self._signal_label_items: list = []
        self._signal_items: list[tuple[str, QGraphicsEllipseItem]] = []
        self._branch_items: list[QGraphicsPathItem] = []
        self._target_items: list = []
        self.follow_train = True
        self._left = 90.0
        self._top = 120.0
        self._min_station_gap = 150.0
        self._max_station_gap = 250.0
        self._distance_scale = 0.12
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
        self._update_detail_visibility()

    def rebuild(self):
        """根据语义模型重建整张运营线路图。"""
        self._scene.clear()
        self._train_items.clear()
        self._dynamic_items.clear()
        self._link_items.clear()
        self._link_label_items.clear()
        self._signal_label_items.clear()
        self._signal_items.clear()
        self._branch_items.clear()
        self._target_items.clear()
        self._station_x.clear()

        if not self.track_data:
            self._scene.setSceneRect(QRectF(0, 0, 1000, 520))
            self._add_text("暂无线路数据", 80, 80, 16, self.TEXT, True)
            return

        self.model = build_semantic_line(self.track_data)
        self._link_mapper = LinkCoordinateMapper(self.track_data)
        self._build_coordinate_scale()
        self._layout_stations()
        last_x = max(self._station_x.values(), default=self._left + 800.0)
        width = last_x + self._left
        height = 560.0
        self._scene.setSceneRect(QRectF(0, 0, width + 280, height))

        self._draw_title()
        self._draw_main_line()
        # 默认保留全部运营要素，只弱化标注；工程模式再展开边界和编号。
        if self.show_details:
            self._draw_link_boundaries()
        self._draw_branch_components(compact=not self.show_details)
        self._draw_signals(compact=not self.show_details)
        self._draw_stations()
        self._draw_summary(width)
        self.resetTransform()
        self._update_detail_visibility()
        self.centerOn(self._left + 420, (self._down_y + self._up_y) / 2)

    def set_train_position(self, car_abs_positions: Iterable[float], controller=None):
        """优先按 Seg→Link 公里标投影列车，旧绝对坐标仅作为回退。"""
        for item in self._train_items:
            self._scene.removeItem(item)
        self._train_items.clear()

        states = tuple(controller.states[:8]) if controller else ()
        fallback_positions = list(car_abs_positions or [])[:8]
        count = max(len(states), len(fallback_positions))
        for idx in range(count):
            link_pos = (
                self._link_mapper.to_link_position(states[idx].position)
                if self._link_mapper and idx < len(states) else None
            )
            if link_pos is None:
                if idx >= len(fallback_positions):
                    continue
                link_pos = float(fallback_positions[idx])
            x = self._x_for_position(link_pos)
            direction = controller.direction if controller else 1
            y = self._down_y if direction > 0 else self._up_y
            rect = QGraphicsRectItem(x - 14, y - 23 - idx * 0.2, 28, 14)
            rect.setBrush(QBrush(self.TRAIN))
            rect.setPen(QPen(QColor("#92400e"), 1.0))
            rect.setZValue(30)
            self._scene.addItem(rect)
            self._train_items.append(rect)
        if self.follow_train and self._train_items:
            self.ensureVisible(self._train_items[0], 80, 60)

    def set_target_marker(self, abs_pos: float):
        """用跨越双线的目标标记表示站点里程，避免猜测 Seg。"""
        for item in self._target_items:
            if item.scene() is self._scene:
                self._scene.removeItem(item)
        self._target_items.clear()
        x = self._x_for_position(abs_pos)
        line = self._scene.addLine(
            x, self._down_y - 34, x, self._up_y + 34,
            QPen(QColor("#dc2626"), 1.8, Qt.DashLine))
        line.setZValue(28)
        label = self._add_text(
            "目标", x, self._down_y - 58, 8,
            QColor("#b91c1c"), True, center=True)
        self._target_items.extend((line, label))

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
            down_occupied = bool(occupied_ids.intersection(link.down_seg_ids))
            up_occupied = bool(occupied_ids.intersection(link.up_seg_ids))
            # 回退兼容：旧 SemanticLink 可能没有方向分离字段
            if not link.down_seg_ids and not link.up_seg_ids:
                any_occupied = bool(occupied_ids.intersection(link.seg_ids))
                down_occupied = any_occupied
                up_occupied = any_occupied
            down_item.setPen(QPen(
                QColor("#dc2626") if down_occupied else self.DOWN_LINE,
                7.0 if down_occupied else 5.0, Qt.SolidLine, Qt.RoundCap))
            up_item.setPen(QPen(
                QColor("#dc2626") if up_occupied else self.UP_LINE,
                7.0 if up_occupied else 5.0, Qt.SolidLine, Qt.RoundCap))

        lanes: dict[tuple[int, int], int] = {}
        palette = ("#f59e0b", "#7c3aed", "#0891b2", "#2563eb", "#0f766e")
        for index, runtime in enumerate(tuple(runtimes)):
            head_state = runtime.controller.states[0] if runtime.controller.states else None
            link_pos = (
                self._link_mapper.to_link_position(head_state.position)
                if self._link_mapper and head_state else None
            )
            x = self._x_for_position(
                link_pos if link_pos is not None else runtime.head_abs)
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
        """按公里标大致比例布局，同时保证短区间仍然清晰可见。"""
        if not self.model:
            return
        for station in self.model.stations:
            self._station_x[station.station_id] = self._x_for_position(
                station.position)

    def _display_interval_width(self, distance_m: float) -> float:
        """把实际站间距压缩到可读范围，兼顾比例和最小显示宽度。"""
        return max(
            self._min_station_gap,
            min(self._max_station_gap, distance_m * self._distance_scale),
        )

    def _draw_title(self):
        self._add_text("运营线路图", 72, 46, 22, self.TEXT, True)
        self._add_text(
            ("Link 主线 · 公里标近似比例 · Seg/道岔分支叠加"
             if self.show_details else
             "双线运营模式 · 车站与实时列车"),
            72, 78, 10, self.MUTED)

    def _draw_main_line(self):
        if not self.model or len(self.model.stations) < 2:
            return
        all_links = [link for links in load_mainline_links().values()
                     for link in links]
        first = self._x_for_position(min(link.start_m for link in all_links))
        last = self._x_for_position(max(link.end_m for link in all_links))

        # 上下行分开绘制，避免看起来只有一个方向。
        self._draw_direction_line(first, last, self._down_y, self.DOWN_LINE, "下行", "→")
        self._draw_direction_line(first, last, self._up_y, self.UP_LINE, "上行", "←")

        for link in self.model.links:
            start_x = self._station_x.get(link.start_station_id)
            end_x = self._station_x.get(link.end_station_id)
            if start_x is None or end_x is None:
                continue
            distance = abs(link.end_pos - link.start_pos)
            down_item = self._scene.addLine(
                start_x, self._down_y, end_x, self._down_y,
                QPen(self.DOWN_LINE, 5.0, Qt.SolidLine, Qt.RoundCap))
            up_item = self._scene.addLine(
                start_x, self._up_y, end_x, self._up_y,
                QPen(self.UP_LINE, 5.0, Qt.SolidLine, Qt.RoundCap))
            down_item.setZValue(3)
            up_item.setZValue(3)
            detail = (
                f"区间: {link.start_station_id} → {link.end_station_id}\n"
                f"长度: {distance / 1000:.2f} km\n"
                f"包含 Link: {self._format_id_summary(link.seg_ids)}"
            )
            down_item.setToolTip(detail)
            up_item.setToolTip(detail)
            self._link_items.append((link, down_item, up_item))

    def _draw_link_boundaries(self):
        """绘制所有 Link 边界；放大后显示编号，悬停可核对完整数据。"""
        if not self._link_mapper:
            return
        for direction, links in load_mainline_links().items():
            y = self._down_y if direction == "down" else self._up_y
            color = self.DOWN_LINE if direction == "down" else self.UP_LINE
            for link in links:
                if self._link_mapper.link_for_segment(link.link_id) is None:
                    continue
                start_x = self._x_for_position(link.start_m)
                end_x = self._x_for_position(link.end_m)
                tick = self._scene.addLine(
                    start_x, y - 8, start_x, y + 8,
                    QPen(self.TEXT, 1.0))
                tick.setZValue(12)
                hit = ImmediateTooltipLineItem(start_x, y, end_x, y)
                hit.setPen(QPen(
                    QColor(0, 0, 0, 1), 14.0, Qt.SolidLine, Qt.RoundCap))
                hit.setZValue(13)
                hit.set_immediate_tooltip(
                    f"Link ID: {link.link_id}\n"
                    f"方向: {'下行' if direction == 'down' else '上行'}\n"
                    f"起点: {link.start_m:.2f} m\n"
                    f"终点: {link.end_m:.2f} m\n"
                    f"长度: {link.length_m:.2f} m")
                self._scene.addItem(hit)
            if links:
                end_x = self._x_for_position(links[-1].end_m)
                self._scene.addLine(
                    end_x, y - 8, end_x, y + 8, QPen(self.TEXT, 1.0))

    def _draw_branch_components(self, compact: bool = False):
        """沿反位 Seg 连通分量绘制完整渡线、折返线和尽头线。"""
        if not self._link_mapper:
            return
        switches, _signals = load_ats_layout()
        components = self._branch_components(switches)
        occupied_lanes: list[list[tuple[float, float]]] = [[], [], []]
        branch_color = QColor("#94a3b8") if compact else self.BRANCH
        pen = QPen(branch_color, 1.8 if compact else 2.6,
                   Qt.SolidLine, Qt.RoundCap)
        for component_seg_ids, attachments in components:
            points = []
            for switch in attachments:
                anchor = self._switch_anchor(
                    switch.merge_seg_id, switch.normal_seg_id,
                    switch.reverse_seg_id)
                reference = (switch.merge_seg_id
                             if self._link_mapper.link_for_segment(
                                 switch.merge_seg_id)
                             else switch.normal_seg_id)
                direction = self._link_mapper.direction_for_segment(reference)
                points.append((self._x_for_position(anchor), direction, switch))
            points.sort(key=lambda item: item[0])
            min_x, max_x = points[0][0], points[-1][0]
            directions = {item[1] for item in points}
            path = QPainterPath()
            direction_counts = {
                direction: sum(item[1] == direction for item in points)
                for direction in directions
            }
            if (len(points) == 4
                    and direction_counts == {"down": 2, "up": 2}):
                # 标准交叉渡线：上下行各两个岔尖，固定画成 X。
                down = sorted((item for item in points if item[1] == "down"),
                              key=lambda item: item[0])
                up = sorted((item for item in points if item[1] == "up"),
                            key=lambda item: item[0])
                path.moveTo(down[0][0], self._down_y)
                path.lineTo(up[1][0], self._up_y)
                path.moveTo(up[0][0], self._up_y)
                path.lineTo(down[1][0], self._down_y)
                role = "交叉渡线"
            elif len(points) == 2 and directions == {"down", "up"}:
                # 单渡线：只保留一条清晰对角线。
                first, second = points
                first_y = self._down_y if first[1] == "down" else self._up_y
                second_y = self._down_y if second[1] == "down" else self._up_y
                path.moveTo(first[0], first_y)
                path.lineTo(second[0], second_y)
                role = "单渡线"
            elif len(points) >= 2 and directions == {"down", "up"}:
                # 多接点折返组件固定收敛到两条正线之间。
                branch_y = (self._down_y + self._up_y) / 2
                path.moveTo(min_x, branch_y)
                path.lineTo(max_x, branch_y)
                for x, direction, _switch in points:
                    base_y = self._down_y if direction == "down" else self._up_y
                    path.moveTo(x, base_y)
                    path.lineTo(x, branch_y)
                role = "折返线"
            elif len(points) == 1:
                # 单入口线路统一放到正线外侧，并以车挡线结束。
                x, direction, _switch = points[0]
                base_y = self._down_y if direction == "down" else self._up_y
                lane = self._claim_branch_lane(x, x + 76, occupied_lanes)
                sign = -1 if direction == "down" else 1
                branch_y = base_y + sign * (54 + lane * 24)
                end_x = x + 76
                path.moveTo(x, base_y)
                path.lineTo(x + 30, branch_y)
                path.lineTo(end_x, branch_y)
                path.moveTo(end_x, branch_y - 7)
                path.lineTo(end_x, branch_y + 7)
                role = "尽头线"
            else:
                # 同方向回接作为侧线，始终位于对应正线外侧。
                direction = points[0][1]
                lane = self._claim_branch_lane(min_x, max_x, occupied_lanes)
                base_y = self._down_y if direction == "down" else self._up_y
                sign = -1 if direction == "down" else 1
                branch_y = base_y + sign * (54 + lane * 24)
                path.moveTo(min_x, branch_y)
                path.lineTo(max_x, branch_y)
                for x, direction, _switch in points:
                    base_y = self._down_y if direction == "down" else self._up_y
                    path.moveTo(x, base_y)
                    path.lineTo(x, branch_y)
                role = "侧线"

            item = ImmediateTooltipPathItem(path)
            item.setPen(pen)
            item.setZValue(8)
            switch_text = ", ".join(
                f"{switch.name}(汇{switch.merge_seg_id}/定{switch.normal_seg_id}/反{switch.reverse_seg_id})"
                for switch in attachments)
            item.set_immediate_tooltip(
                f"{role}\n道岔: {switch_text}\n"
                f"反位线路: {len(component_seg_ids)} 个 Seg\n"
                f"代表 Seg: {self._format_id_summary(component_seg_ids)}")
            self._scene.addItem(item)
            self._branch_items.append(item)
            if not compact:
                for x, direction, switch in points:
                    self._draw_switch_marker(x, direction, switch)

    def _draw_switch_marker(self, x, direction, switch):
        """用统一菱形标出道岔连接点。"""
        y = self._down_y if direction == "down" else self._up_y
        marker_path = QPainterPath()
        marker_path.moveTo(x, y - 5)
        marker_path.lineTo(x + 5, y)
        marker_path.lineTo(x, y + 5)
        marker_path.lineTo(x - 5, y)
        marker_path.closeSubpath()
        marker = ImmediateTooltipPathItem(marker_path)
        marker.setBrush(QBrush(self.STATION_FILL))
        marker.setPen(QPen(self.TEXT, 1.4))
        marker.setZValue(14)
        marker.set_immediate_tooltip(
            f"道岔 {switch.name}\n汇合 Seg: {switch.merge_seg_id}\n"
            f"定位 Seg: {switch.normal_seg_id}\n"
            f"反位 Seg: {switch.reverse_seg_id}")
        self._scene.addItem(marker)

    def _branch_components(self, switches):
        """把正线道岔的反位 Seg 按非主线拓扑合并为完整线路。"""
        main_ids = {
            segment.seg_id for segment in self.track_data.segments
            if self._link_mapper.link_for_segment(segment.seg_id)
        }
        adjacency = {segment.seg_id: set() for segment in self.track_data.segments}
        for segment in self.track_data.segments:
            for neighbor in (segment.start_neighbor, segment.start_lateral,
                             segment.end_neighbor, segment.end_lateral):
                if neighbor in adjacency and neighbor not in (0, 65535):
                    adjacency[segment.seg_id].add(neighbor)
                    adjacency[neighbor].add(segment.seg_id)
        candidates = [
            switch for switch in switches
            if switch.merge_seg_id in main_ids
            and switch.normal_seg_id in main_ids
            and switch.reverse_seg_id not in main_ids
        ]
        grouped: dict[frozenset[int], list] = {}
        for switch in candidates:
            seen = {switch.reverse_seg_id}
            stack = [switch.reverse_seg_id]
            while stack:
                current = stack.pop()
                for neighbor in adjacency[current]:
                    if neighbor not in main_ids and neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            grouped.setdefault(frozenset(seen), []).append(switch)
        return sorted(grouped.items(), key=lambda item: min(
            self._switch_anchor(s.merge_seg_id, s.normal_seg_id,
                                s.reverse_seg_id)
            for s in item[1]))

    @staticmethod
    def _claim_branch_lane(min_x, max_x, occupied_lanes) -> int:
        for lane, spans in enumerate(occupied_lanes):
            if all(max_x < left - 24 or min_x > right + 24
                   for left, right in spans):
                spans.append((min_x, max_x))
                return lane
        occupied_lanes[0].append((min_x, max_x))
        return 0

    @staticmethod
    def _format_id_summary(values, limit: int = 8) -> str:
        """悬浮信息只展示少量代表 ID，避免大连通区撑满屏幕。"""
        ids = sorted(set(values))
        if len(ids) <= limit:
            return ", ".join(map(str, ids))
        head = ", ".join(map(str, ids[:6]))
        tail = ", ".join(map(str, ids[-2:]))
        return f"{head} … {tail}（共 {len(ids)} 个）"

    def _draw_signals(self, compact: bool = False):
        """按信号机所在 Seg 和段内偏移投影到 Link 坐标。"""
        if not self._link_mapper:
            return
        _switches, signals = load_ats_layout()
        for signal in signals:
            position = self._link_mapper.to_link_position(
                TrackPosition(signal.seg_id, signal.offset_m))
            if position is None:
                continue
            x = self._x_for_position(position)
            y = self._down_y if signal.direction == "down" else self._up_y
            sign = -1 if signal.direction == "down" else 1
            lamp_y = y + sign * 20
            stem_color = self.MUTED if compact else self.TEXT
            radius = 3 if compact else 4
            self._scene.addLine(x, y, x, lamp_y, QPen(stem_color, 1.0 if compact else 1.2))
            lamp = ImmediateTooltipEllipseItem(
                x - radius, lamp_y - radius, radius * 2, radius * 2)
            lamp.setBrush(QBrush(QColor("#dc2626")))
            lamp.setPen(QPen(self.TEXT, 1.0))
            lamp.setZValue(22)
            lamp.set_immediate_tooltip(
                f"信号机: {signal.name}\nSeg: {signal.seg_id}\n"
                f"偏移: {signal.offset_m:.2f} m\n"
                f"Link 公里标: {position:.2f} m")
            self._scene.addItem(lamp)
            self._signal_items.append((signal.name, lamp))
            if not compact:
                label = self._add_text(
                    signal.name, x, lamp_y - 18 if sign < 0 else lamp_y + 7,
                    7, self.TEXT, center=True)
                self._signal_label_items.append(label)

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
        switches, signals = load_ats_layout()
        switch_count = sum(
            any(self._link_mapper.link_for_segment(segment_id)
                for segment_id in (switch.normal_seg_id,
                                   switch.reverse_seg_id,
                                   switch.merge_seg_id))
            for switch in switches
        ) if self._link_mapper else 0
        signal_count = sum(
            self._link_mapper.link_for_segment(signal.seg_id) is not None
            for signal in signals
        ) if self._link_mapper else 0
        link_count = sum(len(items) for items in load_mainline_links().values())
        text = (f"车站 {len(self.model.stations)}  |  Link {link_count}  |  "
                f"道岔 {switch_count}  |  信号机 {signal_count}（简化显示）"
                if not self.show_details else
                f"车站 {len(self.model.stations)}  |  Link {link_count}  |  "
                f"正线附近道岔 {switch_count}  |  "
                f"主线信号机 {signal_count}")
        self._add_text(text, 72, 494, 10, self.MUTED)
        self._scene.addLine(72, 476, width + 70, 476, QPen(QColor("#d4dce8"), 1.0))

    def _x_for_position(self, position: float) -> float:
        if not self._coord_points:
            return self._left
        if position <= self._coord_points[0]:
            return self._coord_x[0]
        if position >= self._coord_points[-1]:
            return self._coord_x[-1]
        import bisect
        index = bisect.bisect_right(self._coord_points, position) - 1
        left, right = self._coord_points[index:index + 2]
        ratio = (position - left) / max(1e-9, right - left)
        return self._coord_x[index] + ratio * (
            self._coord_x[index + 1] - self._coord_x[index])

    def _build_coordinate_scale(self):
        """建立全图共享的 Link 公里标→X 分段映射。"""
        points = {
            value
            for links in load_mainline_links().values()
            for link in links
            for value in (link.start_m, link.end_m)
            if self._link_mapper
            and self._link_mapper.link_for_segment(link.link_id)
        }
        if self.model:
            points.update(station.position for station in self.model.stations)
        self._coord_points = sorted(points)
        self._coord_x = [self._left]
        for left, right in zip(self._coord_points, self._coord_points[1:]):
            delta = right - left
            width = max(2.0, min(40.0, delta * self._distance_scale))
            self._coord_x.append(self._coord_x[-1] + width)

    def _switch_anchor(self, *segment_ids: int) -> float:
        links = [self._link_mapper.link_for_segment(segment_id)
                 for segment_id in segment_ids]
        links = [link for link in links if link is not None]
        if len(links) == 1:
            return (links[0].start_m + links[0].end_m) / 2.0
        candidates = []
        for left in links:
            for right in links:
                if left is right:
                    continue
                for a in (left.start_m, left.end_m):
                    for b in (right.start_m, right.end_m):
                        candidates.append((abs(a - b), (a + b) / 2.0))
        return min(candidates)[1]

    def _update_detail_visibility(self):
        detailed = self.transform().m11() >= 1.55
        for item in self._signal_label_items:
            item.setVisible(detailed)
        for item in self._link_label_items:
            item.setVisible(False)

    def set_signal_aspects(self, aspects: dict[str, object]):
        """刷新信号机红黄绿显示，不改变其 Link 投影位置。"""
        colors = {"红灯": "#dc2626", "黄灯": "#eab308", "绿灯": "#16a34a"}
        for name, item in self._signal_items:
            aspect = aspects.get(name)
            value = getattr(aspect, "value", "红灯")
            item.setBrush(QBrush(QColor(colors.get(value, "#dc2626"))))

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
                           occupancy: dict[int, frozenset[str]],
                           signal_aspects: dict[str, object] | None = None):
        runtimes = tuple(runtimes)
        self.view.set_dispatch_state(runtimes, occupancy)
        if signal_aspects is not None:
            self.view.set_signal_aspects(signal_aspects)
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
