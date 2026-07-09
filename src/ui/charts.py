"""车辆仿真可视化组件 — 速度曲线 & 力分量表

提供两个独立组件:
    - SpeedCurveWidget: 使用 QPainter 绘制速度-时间曲线和车钩力曲线（无外部依赖）
    - ForceTableWidget: 展示当前步每节车的力分量明细表
"""

from collections import deque
from typing import List, Optional

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget,
    QTableWidgetItem, QHeaderView, QFrame,
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QPen, QColor, QFont, QFontMetrics, QPainterPath

from src.vehicle.force_report import ForceReport


# ═══════════════════════════════════════════════════════════════
# 颜色常量
# ═══════════════════════════════════════════════════════════════

CURVE_BG = QColor(22, 22, 30)
CURVE_GRID = QColor(45, 50, 60)
CURVE_SPEED = QColor(46, 204, 113)       # 绿色 — 速度
CURVE_LIMIT = QColor(231, 76, 60)        # 红色 — 限速/参考线
CURVE_COUPLER = QColor(241, 196, 15)     # 黄色 — 车钩力
CURVE_TEXT = QColor(200, 205, 215)
CURVE_AXIS = QColor(130, 135, 145)

HISTORY_SECONDS = 60   # 曲线历史窗口 (s)


# ═══════════════════════════════════════════════════════════════
# SpeedCurveWidget — 速度-时间 & 车钩力曲线
# ═══════════════════════════════════════════════════════════════

class SpeedCurveWidget(QWidget):
    """速度-时间 + 车钩力曲线图。

    使用 QPainter 自绘，不依赖 matplotlib。
    数据以 (timestamp, speed_kmh, coupler_kn) 三元组存储。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(200)
        self.setMinimumWidth(400)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), CURVE_BG)
        self.setPalette(pal)

        # 数据缓冲：环形缓冲区，保留最近 HISTORY_SECONDS 秒
        self._data: deque = deque()
        self._max_speed_kmh: float = 80.0   # Y 轴上限（自动扩展）
        self._max_coupler_kn: float = 200.0  # 右轴上限（自动扩展）

    def feed(self, timestamp: float, speed_kmh: float, coupler_kn: float,
             speed_limit_kmh: float = 80.0):
        """喂入一个数据点。

        Args:
            timestamp: 仿真时间 (s)。
            speed_kmh: 头车速度 (km/h)。
            coupler_kn: 最大车钩力 (kN)。
            speed_limit_kmh: 当前限速 (km/h)，用于参考线。
        """
        self._data.append((timestamp, speed_kmh, coupler_kn, speed_limit_kmh))

        # 移除超出窗口的旧数据
        cutoff = timestamp - HISTORY_SECONDS
        while self._data and self._data[0][0] < cutoff:
            self._data.popleft()

        # 自动扩展 Y 轴
        if self._data:
            max_s = max(p[1] for p in self._data) * 1.15
            max_c = max(p[2] for p in self._data) * 1.2
            self._max_speed_kmh = max(80.0, max_s)
            self._max_coupler_kn = max(100.0, max_c)

        self.update()

    def clear(self):
        """清空历史数据。"""
        self._data.clear()
        self.update()

    def paintEvent(self, event):
        if len(self._data) < 2:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        margin_left = 55
        margin_right = 55
        margin_top = 15
        margin_bottom = 30
        plot_w = w - margin_left - margin_right
        plot_h = h - margin_top - margin_bottom

        if plot_w <= 0 or plot_h <= 0:
            painter.end()
            return

        # ── 背景 ──────────────────────────────────────────
        painter.fillRect(0, 0, w, h, CURVE_BG)

        # ── 网格 ──────────────────────────────────────────
        painter.setPen(QPen(CURVE_GRID, 1, Qt.DotLine))
        for i in range(5):
            y = margin_top + plot_h * i / 4
            painter.drawLine(margin_left, int(y), w - margin_right, int(y))
        for i in range(6):
            x = margin_left + plot_w * i / 5
            painter.drawLine(int(x), margin_top, int(x), h - margin_bottom)

        # ── 坐标转换 ──────────────────────────────────────
        t_min = self._data[0][0]
        t_max = self._data[-1][0]
        t_range = max(t_max - t_min, 1.0)

        def to_x(t):
            return margin_left + (t - t_min) / t_range * plot_w

        def to_y_left(v):
            return margin_top + plot_h * (1.0 - v / self._max_speed_kmh)

        def to_y_right(v):
            return margin_top + plot_h * (1.0 - v / self._max_coupler_kn)

        # ── Y 轴标签（左：速度） ─────────────────────────
        painter.setPen(CURVE_TEXT)
        painter.setFont(QFont("Consolas", 8))
        for i in range(5):
            val = self._max_speed_kmh * (4 - i) / 4
            y = margin_top + plot_h * i / 4
            painter.drawText(2, int(y) + 4, f"{val:.0f}")

        # Y 轴标签（右：车钩力 kN）
        for i in range(5):
            val = self._max_coupler_kn * (4 - i) / 4
            y = margin_top + plot_h * i / 4
            painter.drawText(w - margin_right + 3, int(y) + 4, f"{val:.0f}")

        # ── 速度曲线（绿色） ─────────────────────────────
        pen_speed = QPen(CURVE_SPEED, 2.0)
        painter.setPen(pen_speed)
        path_speed = QPainterPath()
        first = True
        for t, s, _, _ in self._data:
            x = to_x(t)
            y = to_y_left(s)
            if first:
                path_speed.moveTo(x, y)
                first = False
            else:
                path_speed.lineTo(x, y)
        painter.drawPath(path_speed)

        # ── 限速参考线（红色虚线） ────────────────────────
        if self._data:
            limit = self._data[-1][3]
            y_limit = to_y_left(limit)
            pen_limit = QPen(CURVE_LIMIT, 1.0, Qt.DashLine)
            painter.setPen(pen_limit)
            painter.drawLine(margin_left, int(y_limit), w - margin_right, int(y_limit))
            painter.drawText(w - margin_right - 30, int(y_limit) - 4, f"限速 {limit:.0f}")

        # ── 车钩力曲线（黄色，右轴） ─────────────────────
        pen_coupler = QPen(CURVE_COUPLER, 1.5, Qt.SolidLine)
        painter.setPen(pen_coupler)
        path_coupler = QPainterPath()
        first = True
        for t, _, c, _ in self._data:
            x = to_x(t)
            y = to_y_right(c)
            if first:
                path_coupler.moveTo(x, y)
                first = False
            else:
                path_coupler.lineTo(x, y)
        painter.drawPath(path_coupler)

        # ── X 轴时间标签 ─────────────────────────────────
        painter.setPen(CURVE_AXIS)
        painter.setFont(QFont("Consolas", 8))
        for i in range(6):
            t_val = t_min + t_range * i / 5
            x = margin_left + plot_w * i / 5
            painter.drawText(int(x) - 15, h - margin_bottom + 16, f"{t_val:.0f}s")

        # ── 图例 ─────────────────────────────────────────
        legend_x = margin_left + 8
        legend_y = margin_top + 4
        painter.setPen(QPen(CURVE_SPEED, 2))
        painter.drawLine(legend_x, legend_y, legend_x + 20, legend_y)
        painter.setPen(CURVE_TEXT)
        painter.drawText(legend_x + 24, legend_y + 4, "速度 km/h")

        legend_y2 = legend_y + 16
        painter.setPen(QPen(CURVE_COUPLER, 1.5))
        painter.drawLine(legend_x, legend_y2, legend_x + 20, legend_y2)
        painter.drawText(legend_x + 24, legend_y2 + 4, "车钩力 kN")

        painter.end()


# ═══════════════════════════════════════════════════════════════
# ForceTableWidget — 每车力分量明细表
# ═══════════════════════════════════════════════════════════════

_FORCE_COLUMNS = [
    "车厢", "速度\nkm/h", "牵引力\nkN", "总制动\nkN",
    "电制动\nkN", "空制动\nkN", "基本阻力\nkN",
    "坡道阻力\nkN", "前车钩\nkN", "后车钩\nkN",
    "合力\nkN", "黏着",
]


class ForceTableWidget(QWidget):
    """展示当前步每节车的力分量明细。

    数据来自 ForceReport，每个仿真步刷新一次。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("forceTableWidget")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        title = QLabel("力学报告 — 逐节车力分量")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        self._table = QTableWidget()
        self._table.setObjectName("forceTable")
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setMinimumHeight(160)

        layout.addWidget(self._table)

    def set_report(self, report: ForceReport):
        """根据 ForceReport 刷新表格。

        Args:
            report: 当前步的完整力学报告。
        """
        n = len(report.cars)
        self._table.setColumnCount(len(_FORCE_COLUMNS))
        self._table.setRowCount(n)
        self._table.setHorizontalHeaderLabels(_FORCE_COLUMNS)

        for i, car in enumerate(report.cars):
            # 车厢号
            self._set_cell(i, 0, str(car.car_index + 1))
            # 速度
            self._set_cell(i, 1, f"{car.velocity * 3.6:.1f}")
            # 牵引力
            self._set_cell(i, 2, f"{car.tractive_force / 1000:.0f}",
                           fg=QColor(37, 99, 235) if car.tractive_force > 1 else None)
            # 总制动力
            self._set_cell(i, 3, f"{abs(car.brake_force) / 1000:.0f}",
                           fg=QColor(220, 38, 38) if abs(car.brake_force) > 1 else None)
            # 电制动
            self._set_cell(i, 4, f"{abs(car.electric_brake_force) / 1000:.0f}")
            # 空制动
            self._set_cell(i, 5, f"{abs(car.friction_brake_force) / 1000:.0f}")
            # 基本阻力
            self._set_cell(i, 6, f"{abs(car.davis_resistance) / 1000:.0f}")
            # 坡道阻力
            grade = car.grade_resistance / 1000
            self._set_cell(i, 7, f"{grade:+.0f}",
                           fg=QColor(220, 38, 38) if grade < -0.5 else None)
            # 前车钩
            self._set_cell(i, 8, f"{car.coupler_force_front / 1000:+.0f}",
                           fg=QColor(245, 158, 11) if abs(car.coupler_force_front) > 1000 else None)
            # 后车钩
            self._set_cell(i, 9, f"{car.coupler_force_rear / 1000:+.0f}",
                           fg=QColor(245, 158, 11) if abs(car.coupler_force_rear) > 1000 else None)
            # 合力
            net = car.net_force / 1000
            self._set_cell(i, 10, f"{net:+.0f}",
                           fg=QColor(37, 99, 235) if net > 0 else QColor(220, 38, 38))
            # 黏着状态
            if car.traction_limited:
                self._set_cell(i, 11, "⚠空转", fg=QColor(220, 38, 38))
            elif car.brake_limited:
                self._set_cell(i, 11, "⚠滑行", fg=QColor(220, 38, 38))
            else:
                self._set_cell(i, 11, "OK", fg=QColor(22, 163, 74))

        # 自适应列宽
        self._table.resizeColumnsToContents()
        # 确保车厢列不会太窄
        if self._table.columnWidth(0) < 36:
            self._table.setColumnWidth(0, 36)

    def _set_cell(self, row: int, col: int, text: str,
                  fg: Optional[QColor] = None):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        if fg is not None:
            item.setForeground(fg)
        font = item.font()
        font.setPointSize(9)
        font.setBold(True)
        item.setFont(font)
        self._table.setItem(row, col, item)

    def clear_data(self):
        """清空表格。"""
        self._table.setRowCount(0)
        self._table.setColumnCount(0)


# ═══════════════════════════════════════════════════════════════
# ForcePanel — 组合面板：力分量表 + 速度曲线
# ═══════════════════════════════════════════════════════════════

class ForcePanel(QWidget):
    """力学分析面板 — 组合 SpeedCurveWidget + ForceTableWidget。

    作为独立 Tab 嵌入主窗口，实时展示车辆仿真数据。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("forcePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 速度 + 车钩力曲线
        self.curve = SpeedCurveWidget()
        self.curve.setMinimumHeight(240)
        layout.addWidget(self.curve, stretch=3)

        # 力分量表
        self.force_table = ForceTableWidget()
        layout.addWidget(self.force_table, stretch=2)

    def feed(self, timestamp: float, speed_kmh: float, coupler_kn: float,
             speed_limit_kmh: float = 80.0):
        """喂入曲线数据点。"""
        self.curve.feed(timestamp, speed_kmh, coupler_kn, speed_limit_kmh)

    def set_report(self, report: ForceReport):
        """刷新力分量表。"""
        self.force_table.set_report(report)

    def clear(self):
        """清空所有数据。"""
        self.curve.clear()
        self.force_table.clear_data()
