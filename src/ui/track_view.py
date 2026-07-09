"""TrackView — 轨道线路可视化组件

使用 QGraphicsView 绘制线路全景图，包括：
  - 区段线段（主线和道岔分支分层显示）
  - 限速区段（颜色编码）
  - 车站/信号机标记
  - 实时列车位置叠加（多节车厢，动车/拖车分色）
默认只显示轨道线和车站名，鼠标悬停时高亮区段并显示详细信息。
"""

from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsLineItem,
    QGraphicsSimpleTextItem, QWidget, QVBoxLayout, QLabel,
    QGraphicsRectItem,
)
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QPen, QBrush, QFont, QPainter, QFontMetrics
from typing import Optional

from src.track.data import TrackData, Segment
from src.track.db_loader import DBLoader
from src.vehicle.vehicle_controller import VehicleController


# 颜色常量
COLOR_MAIN_LINE = QColor(60, 60, 60)
COLOR_BRANCH = QColor(100, 140, 180)
COLOR_HOVER = QColor(255, 120, 0)
COLOR_STATION = QColor(220, 50, 50)
COLOR_SIGNAL = QColor(255, 180, 0)
COLOR_SPEED_HIGH = QColor(120, 200, 120)
COLOR_SPEED_MID = QColor(240, 220, 80)
COLOR_SPEED_LOW = QColor(220, 120, 120)
COLOR_BG = QColor(245, 245, 245)
COLOR_RULER = QColor(150, 150, 150)

# 列车颜色
COLOR_MOTOR_CAR = QColor(37, 99, 235, 200)     # 动车：蓝色
COLOR_TRAILER_CAR = QColor(100, 116, 139, 200)  # 拖车：灰色
COLOR_TRAIN_BORDER = QColor(30, 64, 175, 220)

SCALE = 0.22          # 米 → 像素
BRANCH_OFFSET = 85    # 分支偏移高度 (px)
DIAG_X = 55           # 道岔斜线水平分量 (px)
LINE_WIDTH = 9
HOVER_WIDTH = 14
TRAIN_HEIGHT = 18     # 列车矩形高度 (px)
TRAIN_Y_OFFSET = -9   # 列车中心对齐轨道线（TRAIN_HEIGHT/2），实现重叠效果
SWITCH_SLANT_PX = 30   # 道岔斜线水平偏移 (px)


class SegmentItem(QGraphicsLineItem):
    """可悬停的区段线段"""

    def __init__(self, x1: float, y: float, x2: float,
                 seg: Segment, level: int, scene: QGraphicsScene):
        super().__init__(x1, y, x2, y)
        self.seg = seg
        self.level = level
        self._is_branch = level > 0

        if self._is_branch:
            pen = QPen(COLOR_BRANCH, LINE_WIDTH)
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([8, 6])
            self._default_pen = pen
        else:
            self._default_pen = QPen(COLOR_MAIN_LINE, LINE_WIDTH)
        self._hover_pen = QPen(COLOR_HOVER, HOVER_WIDTH)
        self.setPen(self._default_pen)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setZValue(1)

        # 信息标签（默认隐藏）
        label_txt = (f"Seg {seg.seg_id}  {seg.length:.0f}m"
                     f"  ↗{seg.end_neighbor or '-'}  ↙{seg.start_neighbor or '-'}"
                     f"  {'[岔]' if self._is_branch else ''}")
        self._label = QGraphicsSimpleTextItem(label_txt, self)
        self._label.setFont(QFont("Consolas", 7, QFont.Bold))
        self._label.setBrush(QColor(40, 40, 40))
        self._label.setZValue(10)
        br = self._label.boundingRect()
        self._label_bg = scene.addRect(
            QRectF(br.x() - 2, br.y() - 1, br.width() + 4, br.height() + 2),
            QPen(Qt.NoPen), QBrush(QColor(255, 255, 230, 220)))
        self._label_bg.setZValue(9)
        self._label_bg.setParentItem(self)
        self.set_label_visible(False)

    def set_label_visible(self, visible: bool):
        self._label.setVisible(visible)
        self._label_bg.setVisible(visible)
        if visible:
            mid_x = (self.line().x1() + self.line().x2()) / 2
            label_y = self.line().y1() - 40
            br = self._label.boundingRect()
            self._label.setPos(mid_x - br.width() / 2, label_y)
            self._label_bg.setRect(
                mid_x - br.width() / 2 - 2, label_y - 1,
                br.width() + 4, br.height() + 2)

    def hoverEnterEvent(self, event):
        self.setPen(self._hover_pen)
        self.set_label_visible(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):
        self.setPen(self._default_pen)
        self.set_label_visible(False)
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget):
        painter.setRenderHint(QPainter.Antialiasing)
        super().paint(painter, option, widget)

    def boundingRect(self):
        r = super().boundingRect()
        return r.adjusted(-5, -5, 5, 5)


class TrackView(QGraphicsView):
    """轨道线路可视化视图"""

    # 信号：用户点击了某个线路区段
    #   seg_id: 被点击的区段 ID
    #   abs_pos: 点击位置对应的线路绝对坐标 (m)
    segment_clicked = pyqtSignal(int, float)

    def __init__(self, track_data: TrackData, parent=None):
        super().__init__(parent)
        self.td = track_data
        self.setRenderHints(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setBackgroundBrush(COLOR_BG)
        self.setMouseTracking(True)
        self.setMinimumHeight(560)
        self.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)

        self._branch_levels: dict = {}
        self._segment_items: dict[int, SegmentItem] = {}
        self._speed_rects = []
        self._signal_items = []
        self._station_labels = []

        # 列车覆盖层（独立管理，方便清除重建）
        self._train_items = []
        self._target_items = []  # 目标标记（独立管理，不随列车刷新清除）
        self._last_train_pos_key = None

        # 主线轨道 Y 基准坐标（分支线在此基础上叠加 BRANCH_OFFSET）
        self._base_y = 90

        # 点击导航：记录按下位置，用于区分拖拽和点击
        self._press_pos = None

        self._build()

        total_w = self._x(self.td.total_length())
        if total_w > 0:
            self.resetTransform()
            # 初始视野对准线路起点附近（列车在此出发）
            view_w = self.viewport().width() if self.viewport() else 900
            self.centerOn(self._x(200), 120)

    def _x(self, pos_m: float) -> float:
        return pos_m * SCALE

    def _abs_from_scene_x(self, scene_x: float) -> float:
        """场景 x 坐标 → 线路绝对位置 (m)，对分支段还原 DIAG_X 偏移。"""
        abs_m = scene_x / SCALE
        # 检查是否点击在分支段上（偏移过的），还原绝对位置
        for seg_id, item in self._segment_items.items():
            line = item.line()
            x1, x2 = line.x1(), line.x2()
            if x1 <= scene_x <= x2 and item._is_branch:
                # 分支段：还原偏移
                return abs_m - DIAG_X / SCALE
        return abs_m

    def mousePressEvent(self, event):
        """记录按下位置，用于判断是否为点击（而非拖拽）。"""
        self._press_pos = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """释放鼠标时判断是否为短点击（非拖拽），触发导航。"""
        if self._press_pos is not None:
            delta = event.pos() - self._press_pos
            if delta.manhattanLength() < 4:  # 移动 < 4px 视为点击
                self._handle_click(event)
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _handle_click(self, event):
        """处理线路点击：查找被点击的区段，发出导航信号。"""
        scene_pos = self.mapToScene(event.pos())
        items = self.scene.items(scene_pos)
        for item in items:
            if isinstance(item, SegmentItem):
                abs_pos = self._abs_from_scene_x(scene_pos.x())
                self.segment_clicked.emit(item.seg.seg_id, abs_pos)
                return

    def wheelEvent(self, event):
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1.0 / factor, 1.0 / factor)

    # ── 列车位置叠加 ──────────────────────────────────────────

    def _car_y(self, car_abs: float) -> float:
        """返回指定绝对位置处车厢应绘制的 Y 坐标。

        根据车厢所在的轨道区段，确定其分支层级（主线=0，道岔分支>=1），
        使列车绘制在正确的轨道线上。
        """
        seg_id = self.td.get_seg_id_at(car_abs)
        level = self._branch_levels.get(seg_id, 0)
        return self._base_y + TRAIN_Y_OFFSET + level * BRANCH_OFFSET

    def set_train_position(self, car_abs_positions: list, controller: VehicleController = None):
        """更新列车在线路图上的显示位置，并确保列车在视野内。

        以头车所在的轨道区段确定整列车的 Y 坐标（主线/分支），
        避免因逐车判定导致列车在道岔区段重叠处视觉断裂。

        Args:
            car_abs_positions: 每节车的绝对位置列表 (m)，从头车到尾车排列。
            controller: VehicleController 实例（用于获取编组信息）。
        """
        if controller is None or not car_abs_positions:
            self._clear_train_overlay()
            self._last_train_pos_key = None
            return

        pos_key = tuple(round(p, 1) for p in car_abs_positions)
        if pos_key == self._last_train_pos_key:
            return
        self._last_train_pos_key = pos_key


        self._clear_train_overlay()

        if controller is None or not car_abs_positions:
            return

        n_cars = len(car_abs_positions)
        if n_cars == 0:
            return

        # 以头车所在区段的分支层级确定整列车 Y 坐标
        head_y = self._car_y(car_abs_positions[0])

        # 头车在分支上时，列车整体需要 DIAG_X 偏移以对齐分支轨道线
        head_seg_id = self.td.get_seg_id_at(car_abs_positions[0])
        head_level = self._branch_levels.get(head_seg_id, 0)
        x_offset = DIAG_X if head_level > 0 else 0

        # 从头车到尾车依次绘制
        train_rects = []
        for i in range(n_cars):
            car_config = controller.consist[i]
            car_length_px = car_config.length * SCALE
            car_abs = car_abs_positions[i]

            # 车厢矩形：以 car_abs 为前边界（右边界），向左延伸 car_length_px
            car_x = self._x(car_abs) - car_length_px + x_offset

            # 动车/拖车不同颜色
            if car_config.is_motor:
                fill_color = COLOR_MOTOR_CAR
            else:
                fill_color = COLOR_TRAILER_CAR

            # 矩形：车厢主体（整列车使用统一的 Y = head_y）
            rect = self.scene.addRect(
                QRectF(car_x, head_y, car_length_px, TRAIN_HEIGHT),
                QPen(COLOR_TRAIN_BORDER, 1),
                QBrush(fill_color),
            )
            rect.setZValue(20)
            self._train_items.append(rect)
            train_rects.append(rect)

            # 头车标记
            if i == 0:
                label = QGraphicsSimpleTextItem("▶", rect)
                label.setFont(QFont("Consolas", 9, QFont.Bold))
                label.setBrush(QColor(255, 255, 255))
                label.setPos(3, 1)
                label.setZValue(21)
                self._train_items.append(label)

            # 车厢序号
            idx_label = QGraphicsSimpleTextItem(str(i + 1), rect)
            idx_label.setFont(QFont("Consolas", 7))
            idx_label.setBrush(QColor(255, 255, 255, 180))
            idx_label.setPos(car_length_px / 2 - 5, 1)
            idx_label.setZValue(21)
            self._train_items.append(idx_label)

        # 确保头车在视野内
        if train_rects:
            self.ensureVisible(train_rects[0], xMargin=80, yMargin=60)

    def _clear_train_overlay(self):
        """清除上次绘制的列车图形。"""
        for item in self._train_items:
            # 父图元移除后，子标签会自动脱离 scene，避免重复 remove 触发 Qt 警告。
            if item.scene() is self.scene:
                self.scene.removeItem(item)
        self._train_items.clear()

    # ── 目标标记 ──────────────────────────────────────────────

    def set_target_marker(self, abs_pos: float):
        """在线路上放置目标标记（红色菱形），指示自动驾驶目标位置。

        Args:
            abs_pos: 目标在线路上的绝对位置 (m)。
        """
        self._clear_target_marker()
        x = self._x(abs_pos)
        # 用 seg_id 确定 y（目标可能在主线或侧线上）
        seg_id = self.td.get_seg_id_at(abs_pos)
        level = self._branch_levels.get(seg_id, 0)
        # 侧线目标需要加 DIAG_X 偏移
        if level > 0:
            fork_x = self._find_fork_x(seg_id)
            if fork_x is not None:
                x = fork_x + DIAG_X + self._x(abs_pos - self.td._seg_map[seg_id].abs_start)
        y = level * BRANCH_OFFSET + self._base_y

        # 红色菱形目标标记
        from PyQt5.QtGui import QPolygonF
        s = 7  # 菱形半边长
        diamond = QPolygonF([
            QPointF(x, y - s),
            QPointF(x + s, y),
            QPointF(x, y + s),
            QPointF(x - s, y),
        ])
        target_item = self.scene.addPolygon(
            diamond,
            QPen(QColor(180, 20, 20), 2),
            QBrush(QColor(255, 70, 70, 200)),
        )
        target_item.setZValue(25)
        target_item.setToolTip(f"🎯 自动驾驶目标 ({abs_pos:.0f}m)")
        self._target_items.append(target_item)

    def _clear_target_marker(self):
        """清除目标标记。"""
        for item in self._target_items:
            self.scene.removeItem(item)
        self._target_items.clear()

    # ── 分支层级计算 ──────────────────────────────────────────

    def _compute_branch_levels(self):
        """计算每个区段的分支层级（主线=0，侧线=1+）。

        通过 BFS 从主线入口段出发，沿 forward 边传播相同层级，
        沿 lateral 边递增层级。
        """
        from collections import deque
        td = self.td
        td._seg_map = {s.seg_id: s for s in td.segments}

        # 找主线入口段：start_neighbor=0 且有 end_neighbor 的段
        root_id = None
        for s in td.segments:
            if s.start_neighbor == 0 and s.end_neighbor > 0 and s.end_neighbor != 65535:
                root_id = s.seg_id
                break
        # 兜底：找第一个不受任何 neighbor 引用的段
        if root_id is None:
            all_refs = set()
            for s in td.segments:
                for n in (s.start_neighbor, s.end_neighbor,
                          s.start_lateral, s.end_lateral):
                    if n > 0 and n != 65535:
                        all_refs.add(n)
            for s in td.segments:
                if s.seg_id not in all_refs:
                    root_id = s.seg_id
                    break
        # 最终兜底
        if root_id is None:
            root_id = td.segments[0].seg_id

        visited = set()
        q = deque([root_id])
        visited.add(root_id)
        self._branch_levels[root_id] = 0

        while q:
            sid = q.popleft()
            seg = td._seg_map.get(sid)
            if not seg:
                continue
            cur_level = self._branch_levels.get(sid, 0)

            for nid in (seg.start_neighbor, seg.end_neighbor):
                if nid <= 0 or nid == 65535 or nid not in td._seg_map:
                    continue
                if nid not in visited:
                    visited.add(nid)
                    self._branch_levels[nid] = cur_level
                    q.append(nid)

            for nid in (seg.start_lateral, seg.end_lateral):
                if nid <= 0 or nid == 65535 or nid not in td._seg_map:
                    continue
                if nid not in visited:
                    visited.add(nid)
                    self._branch_levels[nid] = cur_level + 1
                    q.append(nid)
                else:
                    self._branch_levels[nid] = min(
                        self._branch_levels.get(nid, 999), cur_level + 1)

    def _get_level(self, seg_id: int) -> int:
        return self._branch_levels.get(seg_id, 0)

    def _find_fork_x(self, branch_seg_id: int) -> float | None:
        """查找分支段的道岔分岔点在场景中的 x 坐标（像素）。

        遍历所有区段，找到将 branch_seg_id 作为侧向邻居的父段，
        返回分岔点的场景 x 坐标。start_lateral 岔出在父段起点，
        end_lateral 岔出在父段终点。
        """
        td = self.td
        if not td._seg_map:
            td._seg_map = {s.seg_id: s for s in td.segments}
        for seg in td.segments:
            if seg.end_lateral == branch_seg_id:
                return self._x(seg.abs_start + seg.length)
            if seg.start_lateral == branch_seg_id:
                return self._x(seg.abs_start)
        return None

    def _draw_switch_connectors(self, td: TrackData, base_y: float):
        """绘制主线与道岔分支之间的斜向连接线（模拟真实道岔分叉角度）。

        对于每个道岔，从分岔点画一条斜线到分支线起点，并在分岔点标记圆点。
        """
        if not td._seg_map:
            td._seg_map = {s.seg_id: s for s in td.segments}

        connector_pen = QPen(COLOR_BRANCH, 4)
        connector_pen.setStyle(Qt.SolidLine)
        connector_pen.setCapStyle(Qt.RoundCap)

        dot_pen = QPen(COLOR_BRANCH.darker(130), 1)
        dot_brush = QBrush(COLOR_BRANCH)

        for seg in td.segments:
            parent_level = self._get_level(seg.seg_id)
            parent_y = parent_level * BRANCH_OFFSET + base_y

            # ── start_lateral：道岔在父段起点 ──────────────────
            if seg.start_lateral > 0 and seg.start_lateral in td._seg_map:
                child_id = seg.start_lateral
                child_level = self._get_level(child_id)
                if child_level > parent_level:
                    fork_x = self._x(seg.abs_start)
                    child_y = child_level * BRANCH_OFFSET + base_y
                    # 斜线：从分岔点 → 分支线起点
                    self.scene.addLine(
                        fork_x, parent_y, fork_x + DIAG_X, child_y, connector_pen
                    ).setZValue(0)
                    # 分岔点圆点
                    dot = self.scene.addEllipse(
                        fork_x - 4, parent_y - 4, 8, 8, dot_pen, dot_brush
                    )
                    dot.setZValue(2)
                    dot.setToolTip(f"道岔 seg{seg.seg_id}→seg{child_id} (起点岔出)")

            # ── end_lateral：道岔在父段终点 ────────────────────
            if seg.end_lateral > 0 and seg.end_lateral in td._seg_map:
                child_id = seg.end_lateral
                child_level = self._get_level(child_id)
                if child_level > parent_level:
                    fork_x = self._x(seg.abs_start + seg.length)
                    child_y = child_level * BRANCH_OFFSET + base_y
                    # 斜线：从分岔点 → 分支线起点
                    self.scene.addLine(
                        fork_x, parent_y, fork_x + DIAG_X, child_y, connector_pen
                    ).setZValue(0)
                    # 分岔点圆点
                    dot = self.scene.addEllipse(
                        fork_x - 4, parent_y - 4, 8, 8, dot_pen, dot_brush
                    )
                    dot.setZValue(2)
                    dot.setToolTip(f"道岔 seg{seg.seg_id}→seg{child_id} (终点岔出)")

    # ── 场景构建 ──────────────────────────────────────────────

    def _build(self):
        if not self.td.segments:
            self.scene.addText("暂无线路数据")
            return

        td = self.td
        total_len = td.total_length()
        scene_w = self._x(total_len) + 200

        self._compute_branch_levels()
        max_level = max(self._branch_levels.values()) if self._branch_levels else 0
        base_y = self._base_y

        # ── 区段线段 ──────────────────────────────────────────
        for seg in td.segments:
            level = self._get_level(seg.seg_id)
            y = level * BRANCH_OFFSET + base_y

            if level > 0:
                # 分支段：从斜线连接器终点开始（岔出点 + DIAG_X 偏移）
                fork_x = self._find_fork_x(seg.seg_id)
                if fork_x is not None:
                    x1 = fork_x + DIAG_X
                else:
                    x1 = self._x(seg.abs_start)
            else:
                x1 = self._x(seg.abs_start)
            x2 = x1 + self._x(seg.length)

            item = SegmentItem(x1, y, x2, seg, level, self.scene)
            self.scene.addItem(item)
            self._segment_items[seg.seg_id] = item

        # ── 道岔连接线 ──────────────────────────────────────────
        self._draw_switch_connectors(td, base_y)

        # ── 限速区段 ──────────────────────────────────────────
        for sl in td.speed_limits:
            seg = td._seg_map.get(sl.seg_id)
            if not seg:
                continue
            level = self._get_level(sl.seg_id)
            if level > 0:
                fork_x = self._find_fork_x(sl.seg_id)
                if fork_x is not None:
                    x1 = fork_x + DIAG_X + self._x(sl.start_offset)
                    x2 = fork_x + DIAG_X + self._x(sl.end_offset)
                else:
                    x1 = self._x(sl.abs_start)
                    x2 = self._x(sl.abs_end)
            else:
                x1 = self._x(sl.abs_start)
                x2 = self._x(sl.abs_end)
            y = level * BRANCH_OFFSET + base_y + 13

            speed = sl.speed_limit
            if speed >= 18:
                c = QColor(COLOR_SPEED_HIGH)
            elif speed >= 12:
                c = QColor(COLOR_SPEED_MID)
            else:
                c = QColor(COLOR_SPEED_LOW)
            c.setAlpha(160)

            w = max(x2 - x1, 1)
            rect = self.scene.addRect(QRectF(x1, y, w, 7),
                                      QPen(Qt.NoPen), QBrush(c))
            rect.setZValue(0)
            self._speed_rects.append(rect)

        # ── 车站 ──────────────────────────────────────────────
        platform_poses = sorted(set(p.position for p in td.platforms if p.position > 0))
        for i, st in enumerate(td.stations):
            pos = platform_poses[i] if i < len(platform_poses) else st.position
            if pos <= 0:
                continue
            x = self._x(pos)
            y = 26

            dot = self.scene.addEllipse(x - 6, y - 6, 12, 12,
                                        QPen(COLOR_STATION, 2), QBrush(Qt.white))
            dot.setZValue(2)
            self._signal_items.append(dot)

            text = QGraphicsSimpleTextItem(st.name)
            text.setPos(x - 22, y - 46)
            text.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
            text.setBrush(COLOR_STATION)
            text.setZValue(2)
            self.scene.addItem(text)
            self._station_labels.append(text)

        # ── 信号机 ────────────────────────────────────────────
        for sig in td.signals:
            if sig.position <= 0:
                continue
            x = self._x(sig.position)
            y = base_y

            dot = self.scene.addEllipse(x - 4, y - 14, 8, 8,
                                        QPen(COLOR_SIGNAL, 1),
                                        QBrush(QColor(255, 180, 0, 170)))
            dot.setToolTip(f"信号 {sig.signal_id}  dir={sig.direction}")
            dot.setCursor(Qt.WhatsThisCursor)
            dot.setZValue(1)
            self._signal_items.append(dot)

        # ── 公里标 ────────────────────────────────────────────
        ruler_y = base_y + (max_level + 1) * BRANCH_OFFSET + 70
        for km in range(0, int(total_len) + 500, 500):
            x = self._x(km)
            self.scene.addLine(x, ruler_y, x, ruler_y + 8, QPen(COLOR_RULER))
            label = QGraphicsSimpleTextItem(f"{km//1000}.{km%1000:03d}km")
            label.setPos(x - 15, ruler_y + 10)
            label.setFont(QFont("Consolas", 8))
            label.setBrush(COLOR_RULER)
            self.scene.addItem(label)

        # ── 图例 ──────────────────────────────────────────────
        legend_x = scene_w - 140
        legend_y = 10
        items = [
            ("主线", COLOR_MAIN_LINE),
            ("道岔分支", COLOR_BRANCH),
            ("悬停高亮", COLOR_HOVER),
            ("限速(≥18m/s)", COLOR_SPEED_HIGH),
            ("限速(12-18m/s)", COLOR_SPEED_MID),
            ("限速(<12m/s)", COLOR_SPEED_LOW),
            ("信号机", COLOR_SIGNAL),
            ("车站", COLOR_STATION),
            ("动车", COLOR_MOTOR_CAR),
            ("拖车", COLOR_TRAILER_CAR),
        ]
        for i, (name, color) in enumerate(items):
            ly = legend_y + i * 14
            c = QColor(color)
            self.scene.addRect(legend_x, ly, 10, 6, QPen(Qt.NoPen), QBrush(c))
            lbl = QGraphicsSimpleTextItem(name)
            lbl.setPos(legend_x + 13, ly - 3)
            lbl.setFont(QFont("Consolas", 8))
            self.scene.addItem(lbl)

        # 场景矩形：左侧留 200px 余量容纳列车尾部（可能延伸到起点之前）
        scene_margin_left = 200
        self.scene.setSceneRect(-scene_margin_left, -60, scene_w + scene_margin_left, ruler_y + 70)


class TrackViewWidget(QWidget):
    """包装 TrackView 的容器组件，包含统计信息"""

    # 转发 TrackView 的 segment_clicked 信号
    segment_clicked = pyqtSignal(int, float)

    def __init__(self, track_data: Optional[TrackData] = None, parent=None):
        super().__init__(parent)
        self.setObjectName("trackViewWidget")
        self.track_data: Optional[TrackData] = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        self.setLayout(layout)

        self.status_bar = QLabel("加载中...")
        self.status_bar.setObjectName("trackStatusBar")
        layout.addWidget(self.status_bar)

        self.view: Optional[TrackView] = None
        if track_data is None:
            self._load_data()
        else:
            self.set_track_data(track_data)

    def _load_data(self):
        try:
            loader = DBLoader()
            self.track_data = loader.load_from_db()
        except Exception as e:
            self.status_bar.setText(f"加载失败: {e}")
            return

        layout = self.layout()
        if self.view:
            layout.removeWidget(self.view)
            self.view.deleteLater()

        self.view = TrackView(self.track_data, self)
        self.view.segment_clicked.connect(self.segment_clicked.emit)
        layout.addWidget(self.view)

        td = self.track_data
        stats = (
            f"线路总长: {td.total_length():.0f}m | "
            f"区段: {len(td.segments)} | "
            f"车站: {len(td.stations)} | "
            f"限速: {len(td.speed_limits)} | "
            f"信号: {len(td.signals)}"
        )
        self.status_bar.setText(stats)

    def set_track_data(self, track_data: TrackData, source_name: str = ""):
        """使用外部线路数据重建视图，支持主界面数据源切换"""
        self.track_data = track_data
        layout = self.layout()
        if self.view:
            layout.removeWidget(self.view)
            self.view.deleteLater()

        self.view = TrackView(track_data, self)
        self.view.segment_clicked.connect(self.segment_clicked.emit)
        layout.addWidget(self.view)
        self.status_bar.setText(self._format_stats(track_data, source_name))

    def _format_stats(self, td: TrackData, source_name: str = "") -> str:
        """生成线路统计文本"""
        prefix = f"{source_name} | " if source_name else ""
        return (
            f"{prefix}线路总长: {td.total_length():.0f}m | "
            f"区段: {len(td.segments)} | "
            f"车站: {len(td.stations)} | "
            f"限速: {len(td.speed_limits)} | "
            f"信号: {len(td.signals)}"
        )

    def set_train_position(self, car_abs_positions: list, controller: VehicleController = None):
        """更新列车在线路图上的位置。"""
        if self.view is not None:
            self.view.set_train_position(car_abs_positions, controller)

    def refresh(self):
        """刷新当前线路视图；没有外部数据时回退到数据库加载"""
        if self.track_data is None:
            self._load_data()
        else:
            self.set_track_data(self.track_data)
