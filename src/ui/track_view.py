"""TrackView — 轨道线路可视化组件

使用 QGraphicsView 绘制线路全景图，包括：
  - 区段线段（主线和道岔分支分层显示）
  - 限速区段（颜色编码）
  - 车站/信号机标记
默认只显示轨道线和车站名，鼠标悬停时高亮区段并显示详细信息。
"""

from PyQt5.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsLineItem,
    QGraphicsSimpleTextItem, QWidget, QVBoxLayout, QLabel,
)
from PyQt5.QtCore import Qt, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QPen, QBrush, QFont, QPainter, QFontMetrics
from typing import Optional

from src.track.data import TrackData, Segment
from src.track.db_loader import DBLoader


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

SCALE = 0.22          # 米 → 像素
BRANCH_OFFSET = 42    # 分支偏移高度 (px)
LINE_WIDTH = 9
HOVER_WIDTH = 14


class SegmentItem(QGraphicsLineItem):
    """可悬停的区段线段"""

    def __init__(self, x1: float, y: float, x2: float,
                 seg: Segment, level: int, scene: QGraphicsScene):
        super().__init__(x1, y, x2, y)
        self.seg = seg
        self.level = level
        self._is_branch = level > 0

        self._default_pen = QPen(COLOR_BRANCH if self._is_branch else COLOR_MAIN_LINE, LINE_WIDTH)
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
        # 标签背景（用白色矩形垫底）
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
            # 居中显示在区段上方
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

        self._build()

        total_w = self._x(self.td.total_length())
        if total_w > 0:
            # 不自动压缩全线，按可读比例显示局部，剩余线路通过滚动条查看。
            self.resetTransform()
            self.centerOn(min(total_w, 900), 120)

    def _x(self, pos_m: float) -> float:
        return pos_m * SCALE

    def wheelEvent(self, event):
        factor = 1.15
        if event.angleDelta().y() > 0:
            self.scale(factor, factor)
        else:
            self.scale(1.0 / factor, 1.0 / factor)

    # ---- 分支层级计算 ----

    def _compute_branch_levels(self):
        from collections import deque
        td = self.td
        referenced = set()
        for s in td.segments:
            for n in (s.start_neighbor, s.end_neighbor):
                if n > 0 and n != 65535:
                    referenced.add(n)
        root_id = None
        for s in td.segments:
            if s.seg_id not in referenced:
                root_id = s.seg_id
                break
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

    # ---- 场景构建 ----

    def _build(self):
        if not self.td.segments:
            self.scene.addText("暂无线路数据")
            return

        td = self.td
        total_len = td.total_length()
        scene_w = self._x(total_len) + 200

        self._compute_branch_levels()
        max_level = max(self._branch_levels.values()) if self._branch_levels else 0
        base_y = 90  # 主线 Y 坐标

        # ---- 2. 绘制区段线段（可悬停） ----
        for seg in td.segments:
            x1 = self._x(seg.abs_start)
            x2 = self._x(seg.abs_start + seg.length)
            level = self._get_level(seg.seg_id)
            y = level * BRANCH_OFFSET + base_y

            item = SegmentItem(x1, y, x2, seg, level, self.scene)
            self.scene.addItem(item)
            self._segment_items[seg.seg_id] = item

        # ---- 3. 限速区段（半透明条，始终可见但淡化） ----
        for sl in td.speed_limits:
            x1 = self._x(sl.abs_start)
            x2 = self._x(sl.abs_end)
            seg = td._seg_map.get(sl.seg_id)
            if not seg:
                continue
            level = self._get_level(sl.seg_id)
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

        # ---- 4. 车站（始终可见） ----
        platform_poses = sorted(set(p.position for p in td.platforms if p.position > 0))
        for i, st in enumerate(td.stations):
            pos = platform_poses[i] if i < len(platform_poses) else st.position
            if pos <= 0:
                continue
            x = self._x(pos)
            y = 26

            # 圆点
            dot = self.scene.addEllipse(x - 6, y - 6, 12, 12,
                                        QPen(COLOR_STATION, 2), QBrush(Qt.white))
            dot.setZValue(2)
            self._signal_items.append(dot)

            # 名称
            text = QGraphicsSimpleTextItem(st.name)
            text.setPos(x - 22, y - 46)
            text.setFont(QFont("Microsoft YaHei", 9, QFont.Bold))
            text.setBrush(COLOR_STATION)
            text.setZValue(2)
            self.scene.addItem(text)
            self._station_labels.append(text)

        # ---- 5. 信号机（小灰点，悬停时显色） ----
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

        # ---- 6. 公里标 ----
        ruler_y = base_y + (max_level + 1) * BRANCH_OFFSET + 70
        for km in range(0, int(total_len) + 500, 500):
            x = self._x(km)
            self.scene.addLine(x, ruler_y, x, ruler_y + 8, QPen(COLOR_RULER))
            label = QGraphicsSimpleTextItem(f"{km//1000}.{km%1000:03d}km")
            label.setPos(x - 15, ruler_y + 10)
            label.setFont(QFont("Consolas", 8))
            label.setBrush(COLOR_RULER)
            self.scene.addItem(label)

        # ---- 7. 图例 ----
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
        ]
        for i, (name, color) in enumerate(items):
            ly = legend_y + i * 14
            c = QColor(color)
            self.scene.addRect(legend_x, ly, 10, 6, QPen(Qt.NoPen), QBrush(c))
            lbl = QGraphicsSimpleTextItem(name)
            lbl.setPos(legend_x + 13, ly - 3)
            lbl.setFont(QFont("Consolas", 8))
            self.scene.addItem(lbl)

        self.scene.setSceneRect(0, -60, scene_w, ruler_y + 70)


class TrackViewWidget(QWidget):
    """包装 TrackView 的容器组件，包含统计信息"""

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

    def refresh(self):
        """刷新当前线路视图；没有外部数据时回退到数据库加载"""
        if self.track_data is None:
            self._load_data()
        else:
            self.set_track_data(self.track_data)
