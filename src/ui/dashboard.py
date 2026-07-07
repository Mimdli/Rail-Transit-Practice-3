"""状态面板 — 实时显示列车运行状态"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QGridLayout, QLabel, QFrame, QProgressBar
from PyQt5.QtCore import Qt

from src.vehicle.model import VehicleModel, RunningMode
from src.track.data import TrackData
from src.signal.system import SignalSystem
from src.power.supply import PowerSupply, PowerStatus


class StatusIndicator(QFrame):
    """单个状态指示器"""

    def __init__(self, label: str, unit: str = ""):
        super().__init__()
        self.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        layout = QVBoxLayout(self)

        self.label = QLabel(label)
        self.label.setStyleSheet("font-size: 12px; color: #888;")
        self.value = QLabel("--")
        self.value.setStyleSheet("font-size: 24px; font-weight: bold; color: #333;")
        self.value.setAlignment(Qt.AlignCenter)
        unit_label = QLabel(unit)
        unit_label.setStyleSheet("font-size: 11px; color: #aaa;")
        unit_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.label)
        layout.addWidget(self.value)
        layout.addWidget(unit_label)
        layout.setContentsMargins(8, 4, 8, 4)

    def set_value(self, text: str, color: str = "#333"):
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size: 24px; font-weight: bold; color: {color};")


class Dashboard(QWidget):
    """仪表盘 — 显示所有运行状态"""

    def __init__(self, vehicle: VehicleModel, track: TrackData,
                 signal_system: SignalSystem, power_supply: PowerSupply):
        super().__init__()
        self.vehicle = vehicle
        self.track = track
        self.signal_system = signal_system
        self.power_supply = power_supply
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 标题
        title = QLabel("运行状态")
        title.setStyleSheet("font-size: 18px; font-weight: bold; padding: 4px;")
        layout.addWidget(title)

        # 速度、位置
        grid = QGridLayout()
        grid.setSpacing(8)

        self.speed_indicator = StatusIndicator("当前速度", "km/h")
        self.speed_limit_indicator = StatusIndicator("当前限速", "km/h")
        self.position_indicator = StatusIndicator("当前位置", "m")
        self.gradient_indicator = StatusIndicator("当前坡度", "‰")

        grid.addWidget(self.speed_indicator, 0, 0)
        grid.addWidget(self.speed_limit_indicator, 0, 1)
        grid.addWidget(self.position_indicator, 1, 0)
        grid.addWidget(self.gradient_indicator, 1, 1)
        layout.addLayout(grid)

        # 运行信息
        info_grid = QGridLayout()

        self.station_label = QLabel("当前站: --")
        self.next_station_label = QLabel("下一站: --")
        self.distance_label = QLabel("下一站距离: --")
        self.mode_label = QLabel("模式: 手动")
        self.signal_label = QLabel("信号: --")
        self.power_label = QLabel("供电: --")
        self.door_label = QLabel("车门: 关闭")

        for lbl in [self.station_label, self.next_station_label, self.distance_label,
                    self.mode_label, self.signal_label, self.power_label, self.door_label]:
            lbl.setStyleSheet("font-size: 14px; padding: 2px;")

        info_grid.addWidget(self.mode_label, 0, 0)
        info_grid.addWidget(self.signal_label, 0, 1)
        info_grid.addWidget(self.power_label, 0, 2)
        info_grid.addWidget(self.station_label, 1, 0)
        info_grid.addWidget(self.next_station_label, 1, 1)
        info_grid.addWidget(self.distance_label, 1, 2)
        info_grid.addWidget(self.door_label, 2, 0)

        info_frame = QFrame()
        info_frame.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        info_frame.setLayout(info_grid)
        layout.addWidget(info_frame)

        # 线路进度条
        progress_frame = QFrame()
        progress_frame.setFrameStyle(QFrame.StyledPanel | QFrame.Raised)
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.addWidget(QLabel("线路位置"))

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(1000)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(20)
        progress_layout.addWidget(self.progress_bar)

        # 车站标记行（简化显示）
        self.station_markers = QLabel("")
        self.station_markers.setStyleSheet("font-size: 11px; color: #666;")
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

        # 信号
        aspect = self.signal_system.get_aspect_at(pos, self.track.signals)
        self.signal_label.setText(f"信号: {aspect.value}")

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
