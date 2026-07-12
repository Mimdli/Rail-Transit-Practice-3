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


# ═══════════════════════════════════════════════════════════════
# 能耗可视化颜色常量
# ═══════════════════════════════════════════════════════════════

ENERGY_BG = QColor(22, 22, 30)
ENERGY_GRID = QColor(45, 50, 60)
ENERGY_TRACTION = QColor(46, 204, 113)       # 绿色 — 牵引功率
ENERGY_REGEN = QColor(59, 130, 246)          # 蓝色 — 再生功率
ENERGY_NET = QColor(241, 196, 15)            # 黄色 — 净功率
ENERGY_AUX = QColor(168, 85, 247)            # 紫色 — 辅助功率
ENERGY_CUMULATIVE_TRACTION = QColor(34, 197, 94, 180)
ENERGY_CUMULATIVE_TRACTION_LINE = QColor(26, 152, 72)    # darker(130) 等效
ENERGY_CUMULATIVE_REGEN = QColor(59, 130, 246, 180)
ENERGY_CUMULATIVE_REGEN_LINE = QColor(45, 100, 190)      # darker(130) 等效
ENERGY_CUMULATIVE_NET = QColor(250, 204, 21, 200)
ENERGY_TEXT = QColor(200, 205, 215)
ENERGY_AXIS = QColor(130, 135, 145)

MAX_HISTORY_POINTS = 3000  # 最大历史点数，防止 unbounded memory


# ═══════════════════════════════════════════════════════════════
# EnergyCurveWidget — 能耗功率 & 累积能量曲线
# ═══════════════════════════════════════════════════════════════

class EnergyCurveWidget(QWidget):
    """能耗可视化曲线 — 上：瞬时功率 (kW)，下：累积能量 (kWh)。

    使用 QPainter 自绘，不依赖 matplotlib。
    双纵轴布局，实时展示牵引/再生/净功率及累积电能。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(280)
        self.setMinimumWidth(400)
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(self.backgroundRole(), ENERGY_BG)
        self.setPalette(pal)

        # 功率数据缓冲（设上限防止 unbounded memory）
        self._timestamps: deque = deque(maxlen=MAX_HISTORY_POINTS)
        self._traction_kw: deque = deque(maxlen=MAX_HISTORY_POINTS)
        self._regen_kw: deque = deque(maxlen=MAX_HISTORY_POINTS)
        self._aux_kw: deque = deque(maxlen=MAX_HISTORY_POINTS)
        self._net_kw: deque = deque(maxlen=MAX_HISTORY_POINTS)

        # 累积能量 (kWh)
        self._cum_traction: deque = deque(maxlen=MAX_HISTORY_POINTS)
        self._cum_regen: deque = deque(maxlen=MAX_HISTORY_POINTS)
        self._cum_net: deque = deque(maxlen=MAX_HISTORY_POINTS)

        # Y 轴范围
        self._max_power_kw: float = 200.0
        self._max_energy_kwh: float = 1.0

        self._history_seconds = 120  # 功率窗口
        self._error_count = 0

    def feed(self, timestamp: float,
             traction_power_kw: float, regen_power_kw: float,
             aux_power_kw: float, net_power_kw: float,
             cum_traction_kwh: float, cum_regen_kwh: float,
             cum_net_kwh: float):
        """喂入一个能耗数据点。"""
        # 安全 clamp：防止 NaN/Inf 导致 paint 崩溃
        def _safe(v, default=0.0):
            try:
                if v is None:
                    return default
                f = float(v)
                if f != f:  # NaN check
                    return default
                if f == float('inf') or f == float('-inf'):
                    return default
                return f
            except (ValueError, TypeError):
                return default

        self._timestamps.append(_safe(timestamp, 0.0))
        self._traction_kw.append(_safe(traction_power_kw))
        self._regen_kw.append(_safe(regen_power_kw))
        self._aux_kw.append(_safe(aux_power_kw))
        self._net_kw.append(_safe(net_power_kw))
        self._cum_traction.append(_safe(cum_traction_kwh))
        self._cum_regen.append(_safe(cum_regen_kwh))
        self._cum_net.append(_safe(cum_net_kwh))

        # 移除超出窗口的旧数据
        cutoff = timestamp - self._history_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()
            self._traction_kw.popleft()
            self._regen_kw.popleft()
            self._aux_kw.popleft()
            self._net_kw.popleft()
            self._cum_traction.popleft()
            self._cum_regen.popleft()
            self._cum_net.popleft()

        # 自动扩展 Y 轴
        try:
            if self._timestamps:
                max_p = max(
                    max(self._traction_kw, default=0),
                    max(self._regen_kw, default=0),
                    max(self._aux_kw, default=0),
                    abs(min(self._net_kw, default=0)),
                ) * 1.2
                if max_p > 0 and max_p == max_p:  # NaN guard
                    self._max_power_kw = max(200.0, max_p)
                max_e = max(
                    self._cum_traction[-1] if self._cum_traction else 0,
                    self._cum_net[-1] if self._cum_net else 0,
                ) * 1.2
                if max_e > 0 and max_e == max_e:  # NaN guard
                    self._max_energy_kwh = max(1.0, max_e)
        except Exception:
            pass

        self.update()

    def clear(self):
        """清空历史数据。"""
        self._timestamps.clear()
        self._traction_kw.clear()
        self._regen_kw.clear()
        self._aux_kw.clear()
        self._net_kw.clear()
        self._cum_traction.clear()
        self._cum_regen.clear()
        self._cum_net.clear()
        self._max_power_kw = 200.0
        self._max_energy_kwh = 1.0
        self._error_count = 0
        self.update()

    def paintEvent(self, event):
        """自绘能耗曲线 — 带完整错误保护。"""
        try:
            self._paint_safe(event)
        except Exception:
            self._error_count += 1
            if self._error_count <= 3:
                import traceback
                traceback.print_exc()
            # 降级：绘制空白背景 + 错误提示
            try:
                painter = QPainter(self)
                painter.fillRect(0, 0, self.width(), self.height(), ENERGY_BG)
                painter.setPen(QColor(220, 80, 80))
                painter.setFont(QFont("Microsoft YaHei UI", 10))
                painter.drawText(self.rect(), Qt.AlignCenter,
                                 "能耗曲线渲染错误\n请查看控制台日志")
                painter.end()
            except Exception:
                pass

    def _paint_safe(self, event):
        """安全的绘制实现。"""
        if len(self._timestamps) < 2:
            painter = QPainter(self)
            painter.fillRect(0, 0, max(1, self.width()), max(1, self.height()), ENERGY_BG)
            painter.setPen(ENERGY_TEXT)
            try:
                painter.setFont(QFont("Microsoft YaHei UI", 11))
            except Exception:
                pass
            painter.drawText(self.rect(), Qt.AlignCenter, "能耗数据收集中…")
            painter.end()
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            w = max(1, self.width())
            h = max(1, self.height())
            margin_left = 60
            margin_right = 55
            margin_top = 12
            margin_bottom = 20
            mid_gap = 10

            total_h = h - margin_top - margin_bottom - mid_gap
            plot_h = max(1.0, total_h / 2)

            # 背景
            painter.fillRect(0, 0, w, h, ENERGY_BG)

            # 上：瞬时功率
            self._draw_power_plot(painter, margin_left, margin_right,
                                  margin_top, plot_h, w, h)

            # 下：累积能量
            energy_top = margin_top + plot_h + mid_gap
            self._draw_energy_plot(painter, margin_left, margin_right,
                                   energy_top, plot_h, w, h)
        finally:
            painter.end()

    def _draw_power_plot(self, painter, ml, mr, top, ph, w, h):
        """绘制上半部：瞬时功率曲线。"""
        if len(self._timestamps) < 2:
            return
        t_min = self._timestamps[0]
        t_max = self._timestamps[-1]
        t_range = max(t_max - t_min, 1.0)
        plot_w = max(1, w - ml - mr)

        def to_x(t):
            return ml + max(0.0, min(1.0, (t - t_min) / t_range)) * plot_w

        max_p = max(self._max_power_kw, 1.0)

        def to_y(v):
            return top + ph * (1.0 - max(-2.0, min(2.0, v / max_p)))

        # 网格
        painter.setPen(QPen(ENERGY_GRID, 1, Qt.DotLine))
        for i in range(5):
            y = int(top + ph * i / 4)
            painter.drawLine(ml, y, w - mr, y)
        for i in range(6):
            x = int(ml + plot_w * i / 5)
            painter.drawLine(x, int(top), x, int(top + ph))

        # 零线
        y0 = int(to_y(0))
        if int(top) <= y0 <= int(top + ph):
            painter.setPen(QPen(QColor(80, 85, 95), 1, Qt.DashLine))
            painter.drawLine(ml, y0, w - mr, y0)

        # 曲线
        self._draw_line(painter, self._timestamps, self._traction_kw,
                        to_x, to_y, QPen(ENERGY_TRACTION, 1.8))
        self._draw_line(painter, self._timestamps, self._regen_kw,
                        to_x, to_y, QPen(ENERGY_REGEN, 1.5, Qt.DashLine))
        self._draw_line(painter, self._timestamps, self._aux_kw,
                        to_x, to_y, QPen(ENERGY_AUX, 1.0, Qt.DotLine))
        self._draw_line(painter, self._timestamps, self._net_kw,
                        to_x, to_y, QPen(ENERGY_NET, 2.2))

        # Y 轴标签
        painter.setPen(ENERGY_TEXT)
        painter.setFont(QFont("Consolas", 7))
        for i in range(5):
            val = max_p * (4 - i) / 4
            y = int(top + ph * i / 4)
            painter.drawText(2, y + 4, f"{val:.0f}")

        # 标题
        painter.setPen(QColor(160, 165, 175))
        painter.setFont(QFont("Microsoft YaHei UI", 8, 75))
        painter.drawText(ml + 4, int(top) + 13, "瞬时功率 (kW)")

        # X 轴标签
        painter.setPen(ENERGY_AXIS)
        painter.setFont(QFont("Consolas", 7))
        for i in range(6):
            t_val = t_min + t_range * i / 5
            x = int(ml + plot_w * i / 5)
            painter.drawText(x - 15, int(top + ph) + 14, f"{t_val:.0f}s")

        # 图例
        self._draw_legend(painter, ml + 4, int(top) + 28, [
            (ENERGY_TRACTION, "牵引功率"),
            (ENERGY_REGEN, "再生功率"),
            (ENERGY_NET, "净功率"),
            (ENERGY_AUX, "辅助功率"),
        ])

    def _draw_energy_plot(self, painter, ml, mr, top, ph, w, h):
        """绘制下半部：累积能量曲线。"""
        if len(self._timestamps) < 2:
            return
        t_min = self._timestamps[0]
        t_max = self._timestamps[-1]
        t_range = max(t_max - t_min, 1.0)
        plot_w = max(1, w - ml - mr)

        def to_x(t):
            return ml + max(0.0, min(1.0, (t - t_min) / t_range)) * plot_w

        max_e = max(self._max_energy_kwh, 0.001)

        def to_y(v):
            return top + ph * (1.0 - max(0.0, min(2.0, v / max_e)))

        # 网格
        painter.setPen(QPen(ENERGY_GRID, 1, Qt.DotLine))
        for i in range(5):
            y = int(top + ph * i / 4)
            painter.drawLine(ml, y, w - mr, y)
        for i in range(6):
            x = int(ml + plot_w * i / 5)
            painter.drawLine(x, int(top), x, int(top + ph))

        baseline_y = int(top + ph)

        # 累积牵引（绿色区域）
        self._draw_filled_line(painter, self._timestamps, self._cum_traction,
                               to_x, to_y, ENERGY_CUMULATIVE_TRACTION,
                               ENERGY_CUMULATIVE_TRACTION_LINE,
                               baseline_y)

        # 累积净电耗（黄色线）
        self._draw_line(painter, self._timestamps, self._cum_net,
                        to_x, to_y, QPen(ENERGY_CUMULATIVE_NET, 2.2))

        # 累积再生（蓝色区域）
        self._draw_filled_line(painter, self._timestamps, self._cum_regen,
                               to_x, to_y, ENERGY_CUMULATIVE_REGEN,
                               ENERGY_CUMULATIVE_REGEN_LINE,
                               baseline_y)

        # Y 轴标签
        painter.setPen(ENERGY_TEXT)
        painter.setFont(QFont("Consolas", 7))
        for i in range(5):
            val = max_e * (4 - i) / 4
            y = int(top + ph * i / 4)
            painter.drawText(2, y + 4, f"{val:.3f}")

        # 标题
        painter.setPen(QColor(160, 165, 175))
        painter.setFont(QFont("Microsoft YaHei UI", 8, 75))
        painter.drawText(ml + 4, int(top) + 13, "累积能量 (kWh)")

        # 图例
        self._draw_legend(painter, ml + 4, int(top) + 28, [
            (ENERGY_CUMULATIVE_TRACTION, "牵引电耗"),
            (ENERGY_CUMULATIVE_REGEN, "再生回收"),
            (ENERGY_CUMULATIVE_NET, "净电耗"),
        ])

    def _draw_line(self, painter, timestamps, values, to_x, to_y, pen):
        """绘制单条折线。"""
        if len(timestamps) < 2 or len(values) < 2:
            return
        painter.setPen(pen)
        path = QPainterPath()
        first = True
        for t, v in zip(timestamps, values):
            x = to_x(t)
            y = to_y(v)
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        if path.elementCount() >= 2:
            painter.drawPath(path)

    def _draw_filled_line(self, painter, timestamps, values, to_x, to_y,
                          fill_color, line_color, baseline_y):
        """绘制带填充的折线（面积图）。"""
        if len(timestamps) < 2 or len(values) < 2:
            return
        path = QPainterPath()
        first = True
        for t, v in zip(timestamps, values):
            x = to_x(t)
            y = to_y(v)
            if first:
                path.moveTo(x, y)
                first = False
            else:
                path.lineTo(x, y)
        # 闭合到底部
        if path.elementCount() >= 2:
            last_x = to_x(timestamps[-1])
            first_x = to_x(timestamps[0])
            path.lineTo(last_x, baseline_y)
            path.lineTo(first_x, baseline_y)
            path.closeSubpath()

            painter.setPen(Qt.NoPen)
            painter.setBrush(fill_color)
            painter.drawPath(path)

            # 顶部描线
            painter.setPen(QPen(line_color, 1.2))
            painter.setBrush(Qt.NoBrush)
            path2 = QPainterPath()
            first = True
            for t, v in zip(timestamps, values):
                x = to_x(t)
                y = to_y(v)
                if first:
                    path2.moveTo(x, y)
                    first = False
                else:
                    path2.lineTo(x, y)
            if path2.elementCount() >= 2:
                painter.drawPath(path2)

    def _draw_legend(self, painter, x, y, items):
        """绘制图例。"""
        painter.setFont(QFont("Microsoft YaHei UI", 7))
        base_y = y
        for i, (color, label) in enumerate(items):
            ly = base_y + i * 14
            painter.setPen(QPen(color, 2))
            painter.drawLine(x, ly + 4, x + 16, ly + 4)
            painter.setPen(ENERGY_TEXT)
            painter.drawText(x + 20, ly + 7, label)


# ═══════════════════════════════════════════════════════════════
# EnergySummaryWidget — 能耗指标汇总卡片
# ═══════════════════════════════════════════════════════════════

class EnergySummaryWidget(QWidget):
    """行程能耗汇总卡片 — 显示累积能耗指标和再生效率。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("energySummaryWidget")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)

        # 六个指标
        self._indicators: dict[str, QLabel] = {}
        labels = [
            ("牵引电耗", "kWh", "#2ecc71"),
            ("再生回收", "kWh", "#3b82f6"),
            ("摩擦热损", "kWh", "#ef4444"),
            ("辅助电耗", "kWh", "#a855f7"),
            ("净电耗", "kWh", "#f59e0b"),
            ("再生率", "%", "#22d3ee"),
        ]
        for name, unit, color in labels:
            frame = QFrame()
            frame.setStyleSheet(
                "QFrame { background: #1a1a24; border: 1px solid #2a2a38;"
                " border-radius: 6px; padding: 4px; }"
            )
            fl = QVBoxLayout(frame)
            fl.setContentsMargins(8, 4, 8, 4)
            fl.setSpacing(2)

            name_lbl = QLabel(name)
            name_lbl.setStyleSheet("color: #8890a0; font-size: 11px; font-weight: 600;")
            name_lbl.setAlignment(Qt.AlignCenter)
            fl.addWidget(name_lbl)

            value_lbl = QLabel("--")
            value_lbl.setStyleSheet(
                f"color: {color}; font-size: 20px; font-weight: 800;"
            )
            value_lbl.setAlignment(Qt.AlignCenter)
            fl.addWidget(value_lbl)

            unit_lbl = QLabel(unit)
            unit_lbl.setStyleSheet("color: #667085; font-size: 10px;")
            unit_lbl.setAlignment(Qt.AlignCenter)
            fl.addWidget(unit_lbl)

            layout.addWidget(frame)
            self._indicators[name] = value_lbl

    def update_metrics(self,
                       traction_kwh: float = 0,
                       regen_kwh: float = 0,
                       friction_kwh: float = 0,
                       aux_kwh: float = 0,
                       net_kwh: float = 0,
                       regen_ratio: float = 0):
        """更新所有能耗指标。

        Args:
            traction_kwh: 牵引电耗 (kWh)。
            regen_kwh: 再生回收 (kWh)。
            friction_kwh: 摩擦制动热损 (kWh)。
            aux_kwh: 辅助电耗 (kWh)。
            net_kwh: 净电耗 (kWh)。
            regen_ratio: 再生能量回收率 (0.0 ~ 1.0)。
        """
        self._indicators["牵引电耗"].setText(f"{traction_kwh:.3f}")
        self._indicators["再生回收"].setText(f"{regen_kwh:.3f}")
        self._indicators["摩擦热损"].setText(f"{friction_kwh:.3f}")
        self._indicators["辅助电耗"].setText(f"{aux_kwh:.3f}")
        self._indicators["净电耗"].setText(f"{net_kwh:.3f}")
        self._indicators["再生率"].setText(f"{regen_ratio * 100:.1f}")

    def clear(self):
        """清空所有指标。"""
        for lbl in self._indicators.values():
            lbl.setText("--")


# ═══════════════════════════════════════════════════════════════
# EnergyPanel — 能耗分析组合面板
# ═══════════════════════════════════════════════════════════════

class EnergyPanel(QWidget):
    """能耗分析面板 — 组合 EnergyCurveWidget + EnergySummaryWidget。

    作为独立 Tab 嵌入主窗口，实时展示列车能耗数据。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("energyPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # 能耗曲线（功率 + 累积能量）
        self.curve = EnergyCurveWidget()
        self.curve.setMinimumHeight(300)
        layout.addWidget(self.curve, stretch=3)

        # 汇总指标卡片
        self.summary = EnergySummaryWidget()
        self.summary.setMinimumHeight(70)
        self.summary.setMaximumHeight(90)
        layout.addWidget(self.summary, stretch=0)

        # 单位能耗指标
        unit_frame = QFrame()
        unit_frame.setObjectName("energyUnitFrame")
        unit_layout = QHBoxLayout(unit_frame)
        unit_layout.setContentsMargins(12, 4, 12, 4)
        unit_layout.setSpacing(16)

        self._kwh_per_car_km = QLabel("单位电耗: -- kWh/(车·km)")
        self._kwh_per_1000t_km = QLabel("单位电耗: -- kWh/(千吨·km)")
        self._trip_distance = QLabel("行程: -- m")
        for lbl in [self._kwh_per_car_km, self._kwh_per_1000t_km, self._trip_distance]:
            lbl.setStyleSheet("color: #8890a0; font-size: 13px; font-weight: 600;")
            unit_layout.addWidget(lbl)
        unit_layout.addStretch()
        layout.addWidget(unit_frame)

    def feed(self, timestamp: float,
             traction_power_kw: float, regen_power_kw: float,
             aux_power_kw: float, net_power_kw: float,
             cum_traction_kwh: float, cum_regen_kwh: float,
             cum_net_kwh: float):
        """喂入曲线数据点。"""
        self.curve.feed(timestamp, traction_power_kw, regen_power_kw,
                        aux_power_kw, net_power_kw,
                        cum_traction_kwh, cum_regen_kwh, cum_net_kwh)

    def update_summary(self,
                       traction_kwh: float, regen_kwh: float,
                       friction_kwh: float, aux_kwh: float,
                       net_kwh: float, regen_ratio: float,
                       kwh_per_car_km: float = 0,
                       kwh_per_1000t_km: float = 0,
                       distance_m: float = 0):
        """刷新汇总指标。"""
        self.summary.update_metrics(
            traction_kwh=traction_kwh,
            regen_kwh=regen_kwh,
            friction_kwh=friction_kwh,
            aux_kwh=aux_kwh,
            net_kwh=net_kwh,
            regen_ratio=regen_ratio,
        )
        self._kwh_per_car_km.setText(f"单位电耗: {kwh_per_car_km:.4f} kWh/(车·km)")
        self._kwh_per_1000t_km.setText(f"单位电耗: {kwh_per_1000t_km:.2f} kWh/(千吨·km)")
        self._trip_distance.setText(f"行程: {distance_m:.0f} m")

    def clear(self):
        """清空所有数据。"""
        self.curve.clear()
        self.summary.clear()
        self._kwh_per_car_km.setText("单位电耗: -- kWh/(车·km)")
        self._kwh_per_1000t_km.setText("单位电耗: -- kWh/(千吨·km)")
        self._trip_distance.setText("行程: -- m")
