"""状态面板 — 实时显示列车运行状态"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame, QProgressBar
from PyQt5.QtCore import Qt

from src.vehicle.model import VehicleModel, RunningMode
from src.track.data import TrackData
from src.signal.system import SignalSystem, SignalAspect
from src.power.supply import PowerSupply, PowerStatus


class StatusIndicator(QFrame):
    """单个状态指示器"""

    def __init__(self, label: str, unit: str = ""):
        super().__init__()
        self.setObjectName("statusIndicator")
        layout = QVBoxLayout(self)
        layout.setSpacing(4)

        self.label = QLabel(label)
        self.label.setObjectName("indicatorLabel")
        self.value = QLabel("--")
        self.value.setObjectName("indicatorValue")
        self.value.setAlignment(Qt.AlignCenter)
        unit_label = QLabel(unit)
        unit_label.setObjectName("indicatorUnit")
        unit_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(unit_label)
        layout.setContentsMargins(14, 10, 14, 10)

    def set_value(self, text: str, color: str = "#333"):
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size: 34px; font-weight: 800; color: {color};")


class Dashboard(QWidget):
    """仪表盘 — 显示所有运行状态"""

    def __init__(self, vehicle: VehicleModel, track: TrackData,
                 signal_system: SignalSystem, power_supply: PowerSupply):
        super().__init__()
        self.vehicle = vehicle
        self.track = track
        self.signal_system = signal_system
        self.power_supply = power_supply
        self.setObjectName("dashboard")
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(16, 16, 12, 16)

        # 标题
        title = QLabel("运行状态")
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        # 速度、位置
        grid = QGridLayout()
        grid.setSpacing(12)

        self.speed_indicator = StatusIndicator("当前速度", "km/h")
        self.speed_limit_indicator = StatusIndicator("当前限速", "km/h")
        self.position_indicator = StatusIndicator("当前位置", "m")
        self.gradient_indicator = StatusIndicator("当前坡度", "‰")

        grid.addWidget(self.speed_indicator, 0, 0)
        grid.addWidget(self.speed_limit_indicator, 0, 1)
        grid.addWidget(self.position_indicator, 0, 2)
        grid.addWidget(self.gradient_indicator, 0, 3)
        layout.addLayout(grid)

        # 运行信息
        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(18)
        info_grid.setVerticalSpacing(10)

        self.station_label = QLabel("当前站: --")
        self.next_station_label = QLabel("下一站: --")
        self.distance_label = QLabel("下一站距离: --")
        self.mode_label = QLabel("模式: 手动")
        self.signal_label = QLabel("信号: --")
        self.power_label = QLabel("供电: --")
        self.door_label = QLabel("车门: 关闭")

        for lbl in [self.station_label, self.next_station_label, self.distance_label,
                    self.mode_label, self.signal_label, self.power_label, self.door_label]:
            lbl.setObjectName("infoLabel")

        info_grid.addWidget(self.mode_label, 0, 0)
        info_grid.addWidget(self.signal_label, 0, 1)
        info_grid.addWidget(self.power_label, 0, 2)
        info_grid.addWidget(self.station_label, 1, 0)
        info_grid.addWidget(self.next_station_label, 1, 1)
        info_grid.addWidget(self.distance_label, 1, 2)
        info_grid.addWidget(self.door_label, 2, 0)

        info_frame = QFrame()
        info_frame.setObjectName("infoPanel")
        info_frame.setMinimumHeight(116)
        info_frame.setLayout(info_grid)
        layout.addWidget(info_frame)

        # 前方信号序列，展示闭塞防护的连续状态。
        signal_frame = QFrame()
        signal_frame.setObjectName("infoPanel")
        signal_layout = QVBoxLayout(signal_frame)
        signal_layout.setSpacing(10)
        signal_layout.setContentsMargins(16, 14, 16, 14)
        signal_title = QLabel("前方信号序列")
        signal_title.setObjectName("sectionTitle")
        self.signal_overview_label = QLabel("--")
        self.signal_overview_label.setObjectName("signalOverview")
        self.signal_overview_label.setTextFormat(Qt.RichText)
        self.signal_overview_label.setWordWrap(True)
        signal_layout.addWidget(signal_title)
        signal_layout.addWidget(self.signal_overview_label)
        layout.addWidget(signal_frame)

        # 线路进度条
        progress_frame = QFrame()
        progress_frame.setObjectName("infoPanel")
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setSpacing(10)
        progress_layout.setContentsMargins(16, 14, 16, 14)
        progress_title = QLabel("线路位置")
        progress_title.setObjectName("sectionTitle")
        progress_layout.addWidget(progress_title)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(1000)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(26)
        progress_layout.addWidget(self.progress_bar)

        # 车站标记行（简化显示）
        self.station_markers = QLabel("")
        self.station_markers.setObjectName("stationMarkers")
        progress_layout.addWidget(self.station_markers)
        layout.addWidget(progress_frame)

        layout.addStretch()

    def refresh(self):
        """刷新所有显示"""
        v = self.vehicle
        pos = v.position

        # 速度
        speed = v.get_speed_kmh()
        limit = v.current_speed_limit * 3.6
        speed_color = "#e74c3c" if speed > limit * 0.95 else "#2ecc71"
        self.speed_indicator.set_value(f"{speed:.1f}", speed_color)
        self.speed_limit_indicator.set_value(f"{limit:.0f}")
        self.position_indicator.set_value(f"{pos:.0f}")
        self.gradient_indicator.set_value(f"{v.current_gradient:+.1f}")

        # 运行模式
        mode = "自动" if v.running_mode == RunningMode.AUTOMATIC else "手动"
        self.mode_label.setText(f"模式: {mode}")

        # 信号显示看全线最近前方信号，防护限速仍由信号系统的默认范围控制。
        next_signal = self.signal_system.get_nearest_signal_ahead(
            pos, self.track.signals, look_ahead=float("inf")
        )
        if next_signal:
            aspect = self.signal_system.get_signal_aspect(next_signal)
            distance = next_signal.position - pos
            self.signal_label.setText(f"前方信号 {next_signal.signal_id}: {aspect.value} ({distance:.0f} m)")
        else:
            aspect = self.signal_system.get_aspect_at(pos, self.track.signals)
            self.signal_label.setText(f"信号: {aspect.value}")
        self._refresh_signal_overview(pos)

        # 供电
        self.power_label.setText(f"供电: {self.power_supply.status.value}")

        # 车门
        if v.left_door_open:
            door_text = "左门开"
        elif v.right_door_open:
            door_text = "右门开"
        else:
            door_text = "全部关闭"
        self.door_label.setText(f"车门: {door_text}")

        # 车站信息
        station = self.track.get_station_at(pos)
        self.station_label.setText(f"当前站: {station.name if station else '区间'}")

        next_station = self.track.get_nearest_station_ahead(pos)
        if next_station:
            dist = next_station.position - pos
            self.next_station_label.setText(f"下一站: {next_station.name}")
            self.distance_label.setText(f"下一站距离: {dist:.0f} m")
        else:
            self.next_station_label.setText("下一站: 终点")
            self.distance_label.setText("下一站距离: --")

        # 进度条
        total = self.track.total_length()
        if total > 0:
            progress = int(pos / total * 1000)
            self.progress_bar.setValue(min(progress, 1000))
            self._refresh_station_markers(total)

    def _refresh_signal_overview(self, position: float):
        """刷新前方若干信号机的红黄绿状态摘要"""
        ahead = sorted(
            (sig for sig in self.track.signals if sig.position >= position),
            key=lambda sig: sig.position,
        )[:5]
        if not ahead:
            self.signal_overview_label.setText("无前方信号")
            return

        chunks = []
        for sig in ahead:
            aspect = self.signal_system.get_signal_aspect(sig)
            color = self._aspect_color(aspect)
            distance = sig.position - position
            chunks.append(
                f"<span style='color:{color}; font-weight:700;'>●</span> "
                f"{sig.signal_id} {aspect.value} <span style='color:#667085;'>({distance:.0f}m)</span>"
            )
        self.signal_overview_label.setText("　".join(chunks))

    def _aspect_color(self, aspect: SignalAspect) -> str:
        """信号状态对应的界面颜色"""
        if aspect == SignalAspect.RED:
            return "#dc2626"
        if aspect == SignalAspect.YELLOW:
            return "#d97706"
        return "#16a34a"

    def _refresh_station_markers(self, total_length: float):
        """用文本标记车站位置，辅助理解线路进度"""
        names = [f"{station.name} {station.position / total_length * 100:.0f}%" for station in self.track.stations]
        self.station_markers.setText("  |  ".join(names))
