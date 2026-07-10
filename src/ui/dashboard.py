"""状态面板 — 实时显示列车运行状态（新版多体动力学）

展示内容:
    - 速度 / 限速 / 位置 / 坡度（原有）
    - 加速度 / 牵引力 / 制动力 / 车钩力（新增）
    - 电空制动拆分 / 黏着状态（新增）
    - 编组信息 / 载荷等级（新增）
    - 车站 / 信号 / 供电 / 车门 / 线路进度
"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QFrame, QProgressBar
from PyQt5.QtCore import Qt

from typing import TYPE_CHECKING
from src.vehicle.vehicle_controller import VehicleController
from src.vehicle.enums import RunningMode, LoadLevel, StationPhase, VehicleState
from src.vehicle.force_report import ForceReport, CarForceReport
from src.common.track_position import ITrackQuery
from src.track.data import TrackData
from src.signal.system import SignalSystem, SignalAspect
from src.power.supply import PowerSupply

if TYPE_CHECKING:
    from src.vehicle.auto_drive import AutoDriveController
    from src.vehicle.route_manager import RouteManager


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
        layout.setContentsMargins(8, 4, 8, 4)

    def set_value(self, text: str, color: str = "#333"):
        self.value.setText(text)
        self.value.setStyleSheet(f"font-size: 28px; font-weight: 800; color: {color};")


class Dashboard(QWidget):
    """仪表盘 — 显示所有运行状态（新版多体动力学）"""

    def __init__(self, controller: VehicleController, track: TrackData,
                 track_adapter: ITrackQuery,
                 signal_system: SignalSystem, power_supply: PowerSupply,
                 auto_drive: "AutoDriveController | None" = None,
                 route_manager: "RouteManager | None" = None):
        super().__init__()
        self.controller = controller
        self.track = track
        self.track_adapter = track_adapter
        self.signal_system = signal_system
        self.power_supply = power_supply
        self.auto_drive = auto_drive
        self.route_manager = route_manager
        self._last_report: ForceReport = None
        self.setObjectName("dashboard")
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(12, 10, 8, 10)

        # 标题行：名称 + 编组信息
        title_row = QHBoxLayout()
        self.title_label = QLabel("运行状态 · 1车")
        self.title_label.setObjectName("pageTitle")
        self.consist_label = QLabel(self._consist_text())
        self.consist_label.setObjectName("indicatorLabel")
        self.consist_label.setStyleSheet("color: #667085; font-size: 13px; font-weight: 700; padding-top: 4px;")
        title_row.addWidget(self.title_label)
        title_row.addStretch()
        title_row.addWidget(self.consist_label)
        layout.addLayout(title_row)

        # ── 第一行：速度 / 限速 / 位置 / 坡度 ────────────────────
        grid = QGridLayout()
        grid.setSpacing(6)

        self.speed_indicator = StatusIndicator("当前速度", "km/h")
        self.speed_limit_indicator = StatusIndicator("当前限速", "km/h")
        self.position_indicator = StatusIndicator("当前位置", "m")
        self.gradient_indicator = StatusIndicator("当前坡度", "‰")

        grid.addWidget(self.speed_indicator, 0, 0)
        grid.addWidget(self.speed_limit_indicator, 0, 1)
        grid.addWidget(self.position_indicator, 0, 2)
        grid.addWidget(self.gradient_indicator, 0, 3)
        layout.addLayout(grid)

        # ── 第二行：加速度 / 牵引力 / 制动力 / 车钩力（新增） ──
        grid2 = QGridLayout()
        grid2.setSpacing(6)

        self.accel_indicator = StatusIndicator("加速度", "m/s²")
        self.traction_indicator = StatusIndicator("牵引力", "kN")
        self.brake_indicator = StatusIndicator("总制动力", "kN")
        self.coupler_indicator = StatusIndicator("最大车钩力", "kN")

        grid2.addWidget(self.accel_indicator, 0, 0)
        grid2.addWidget(self.traction_indicator, 0, 1)
        grid2.addWidget(self.brake_indicator, 0, 2)
        grid2.addWidget(self.coupler_indicator, 0, 3)
        layout.addLayout(grid2)

        # ── 电空制动 + 黏着状态（新增） ──────────────────────────
        detail_grid = QGridLayout()
        detail_grid.setHorizontalSpacing(14)
        detail_grid.setVerticalSpacing(6)
        detail_grid.setContentsMargins(10, 8, 10, 8)

        self.electric_brake_label = QLabel("电制动: --")
        self.friction_brake_label = QLabel("空制动: --")
        self.adhesion_label = QLabel("黏着: --")
        self.load_label = QLabel("载荷: AW2")

        for lbl in [self.electric_brake_label, self.friction_brake_label,
                     self.adhesion_label, self.load_label]:
            lbl.setObjectName("infoLabel")
            lbl.setMinimumHeight(22)

        detail_grid.addWidget(self.electric_brake_label, 0, 0)
        detail_grid.addWidget(self.friction_brake_label, 0, 1)
        detail_grid.addWidget(self.adhesion_label, 0, 2)
        detail_grid.addWidget(self.load_label, 0, 3)

        detail_frame = QFrame()
        detail_frame.setObjectName("infoPanel")
        detail_frame.setMinimumHeight(44)
        detail_frame.setMaximumHeight(70)
        detail_frame.setLayout(detail_grid)
        layout.addWidget(detail_frame)

        # ── 运行信息（原有） ─────────────────────────────────────
        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(12)
        info_grid.setVerticalSpacing(6)
        info_grid.setContentsMargins(10, 8, 10, 8)

        self.station_label = QLabel("当前站: --")
        self.next_station_label = QLabel("下一站: --")
        self.distance_label = QLabel("下一站距离: --")
        self.mode_label = QLabel("模式: 手动")
        self.route_label = QLabel("进路: --")
        self.signal_label = QLabel("信号: --")
        self.power_label = QLabel("供电: --")
        self.door_label = QLabel("车门: 关闭")

        for lbl in [self.station_label, self.next_station_label, self.distance_label,
                     self.mode_label, self.route_label, self.signal_label, self.power_label, self.door_label]:
            lbl.setObjectName("infoLabel")
            lbl.setMinimumHeight(22)

        info_grid.addWidget(self.mode_label, 0, 0)
        info_grid.addWidget(self.route_label, 0, 1)
        info_grid.addWidget(self.signal_label, 0, 2)
        info_grid.addWidget(self.power_label, 1, 0)
        info_grid.addWidget(self.station_label, 1, 1)
        info_grid.addWidget(self.next_station_label, 1, 2)
        info_grid.addWidget(self.distance_label, 2, 0)
        info_grid.addWidget(self.door_label, 2, 1)

        info_frame = QFrame()
        info_frame.setObjectName("infoPanel")
        info_frame.setMinimumHeight(96)
        info_frame.setMaximumHeight(135)
        info_frame.setLayout(info_grid)
        layout.addWidget(info_frame)

        # ── 前方信号序列 ────────────────────────────────────────
        signal_frame = QFrame()
        signal_frame.setObjectName("infoPanel")
        signal_layout = QVBoxLayout(signal_frame)
        signal_layout.setSpacing(4)
        signal_layout.setContentsMargins(12, 8, 12, 8)
        signal_title = QLabel("前方信号序列")
        signal_title.setObjectName("sectionTitle")
        self.signal_overview_label = QLabel("--")
        self.signal_overview_label.setObjectName("signalOverview")
        self.signal_overview_label.setTextFormat(Qt.RichText)
        self.signal_overview_label.setWordWrap(True)
        signal_layout.addWidget(signal_title)
        signal_layout.addWidget(self.signal_overview_label)
        layout.addWidget(signal_frame)

        # ── 线路进度条 ──────────────────────────────────────────
        progress_frame = QFrame()
        progress_frame.setObjectName("infoPanel")
        progress_layout = QVBoxLayout(progress_frame)
        progress_layout.setSpacing(4)
        progress_layout.setContentsMargins(12, 8, 12, 8)
        progress_title = QLabel("线路位置")
        progress_title.setObjectName("sectionTitle")
        progress_layout.addWidget(progress_title)

        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(1000)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(26)
        progress_layout.addWidget(self.progress_bar)

        self.station_markers = QLabel("")
        self.station_markers.setObjectName("stationMarkers")
        progress_layout.addWidget(self.station_markers)
        layout.addWidget(progress_frame)

        layout.addStretch()

    def bind_runtime(self, controller: VehicleController,
                     track_adapter: ITrackQuery, train_id: str):
        """切换运行仿真页正在监控的列车。"""
        self.controller = controller
        self.track_adapter = track_adapter
        self.train_id = train_id
        self.title_label.setText(f"运行状态 · {train_id}")
        self.refresh(controller.last_report)

    # ── 刷新 ────────────────────────────────────────────────────

    def refresh(self, report: ForceReport = None):
        """刷新所有显示。

        Args:
            report: 当前步的 ForceReport。为 None 时仅刷新基本信息。
        """
        self._last_report = report
        ctrl = self.controller

        # ── 头车绝对位置 ───────────────────────────────────────
        head_abs = 0.0
        if ctrl.states:
            head_abs = self.track_adapter.to_absolute(ctrl.states[0].position)

        # ── 速度 ───────────────────────────────────────────────
        speed = ctrl.head_speed_kmh
        track_limit = self.track.get_speed_limit_at(head_abs)
        limit_kmh = track_limit * 3.6
        speed_color = "#e74c3c" if speed > limit_kmh * 0.95 else "#2ecc71"
        self.speed_indicator.set_value(f"{speed:.1f}", speed_color)
        self.speed_limit_indicator.set_value(f"{limit_kmh:.0f}")
        self.position_indicator.set_value(f"{head_abs:.0f}")

        # ── 坡度 ───────────────────────────────────────────────
        gradient = self.track.get_gradient_at(head_abs)
        self.gradient_indicator.set_value(f"{gradient:+.1f}")

        # ── 加速度 ─────────────────────────────────────────────
        if ctrl.states:
            accel = ctrl.states[0].acceleration
            accel_color = "#2ecc71" if accel > 0 else ("#e74c3c" if accel < -0.5 else "#333")
            self.accel_indicator.set_value(f"{accel:+.2f}", accel_color)
        else:
            self.accel_indicator.set_value("--")

        # ── 力分量（从 report 取头车数据） ──────────────────────
        if report is not None and report.cars:
            total_trac = sum(c.tractive_force for c in report.cars)
            total_brake = sum(abs(c.brake_force) for c in report.cars)
            self.traction_indicator.set_value(f"{total_trac / 1000:.0f}",
                                              "#2563eb" if total_trac > 1 else "#333")
            self.brake_indicator.set_value(f"{total_brake / 1000:.0f}",
                                           "#dc2626" if total_brake > 1 else "#333")

            # 电空制动拆分
            e_brake_kn = sum(abs(c.electric_brake_force) for c in report.cars) / 1000
            f_brake_kn = sum(abs(c.friction_brake_force) for c in report.cars) / 1000
            self.electric_brake_label.setText(f"电制动: {e_brake_kn:.0f} kN")
            self.friction_brake_label.setText(f"空制动: {f_brake_kn:.0f} kN")

            # 黏着状态
            if any(c.traction_limited for c in report.cars):
                self.adhesion_label.setText("黏着: ⚠ 空转!")
                self.adhesion_label.setStyleSheet("color: #dc2626; font-size: 17px; font-weight: 700;")
            elif any(c.brake_limited for c in report.cars):
                self.adhesion_label.setText("黏着: ⚠ 滑行!")
                self.adhesion_label.setStyleSheet("color: #dc2626; font-size: 17px; font-weight: 700;")
            else:
                self.adhesion_label.setText("黏着: OK")
                self.adhesion_label.setStyleSheet("color: #16a34a; font-size: 17px; font-weight: 650;")
        else:
            self.traction_indicator.set_value("--")
            self.brake_indicator.set_value("--")
            self.electric_brake_label.setText("电制动: --")
            self.friction_brake_label.setText("空制动: --")
            self.adhesion_label.setText("黏着: --")

        # ── 最大车钩力 ─────────────────────────────────────────
        if report is not None:
            max_coupler_kn = report.max_coupler_force / 1000
            coupler_color = "#f59e0b" if max_coupler_kn > 200 else "#333"
            self.coupler_indicator.set_value(f"{max_coupler_kn:.0f}", coupler_color)
        else:
            self.coupler_indicator.set_value("--")

        # ── 运行模式 + 停站状态 + 车辆状态 ───────────────────────
        mode = "自动" if ctrl.running_mode == RunningMode.AUTOMATIC else "手动"
        # 车辆运行状态
        state_names = {
            VehicleState.INIT: "初始化",
            VehicleState.STOPPED: "停止",
            VehicleState.STARTING: "启动中",
            VehicleState.MOVING: "运行中",
            VehicleState.COASTING: "惰行",
            VehicleState.BRAKING: "制动中",
            VehicleState.EMERGENCY: "紧急制动",
        }
        state_text = state_names.get(ctrl.vehicle_state, "未知")
        mode += f" · {state_text}"
        if self.auto_drive is not None:
            phase = self.auto_drive.station_phase
            if phase == StationPhase.DWELL:
                remaining = self.auto_drive.dwell_remaining
                mode += f" · 停站中 {remaining:.0f}s"
            elif phase == StationPhase.APPROACHING:
                mode += " · 进站中"
            elif phase == StationPhase.DEPARTING:
                mode += " · 准备发车"
        self.mode_label.setText(f"模式: {mode}")

        # ── 进路（独立于驾驶模式） ────────────────────────────────
        route_text = "进路: --"
        if self.route_manager is not None:
            active_route = self.route_manager.active_route
            if active_route is not None:
                if active_route.is_auto:
                    route_text = "进路: 自动（系统算路）"
                else:
                    route_text = f"进路: {active_route.name}"
        self.route_label.setText(route_text)

        # ── 信号 ───────────────────────────────────────────────
        next_signal = self.signal_system.get_nearest_signal_ahead(head_abs, self.track.signals)
        if next_signal:
            aspect = self.signal_system.get_signal_aspect(next_signal)
            distance = direction * (next_signal.position - head_abs)
            self.signal_label.setText(f"前方信号 {next_signal.signal_id}: {aspect.value} ({distance:.0f} m)")
        else:
            aspect = self.signal_system.get_aspect_at(head_abs, self.track.signals)
            self.signal_label.setText(f"信号: {aspect.value}")
        self._refresh_signal_overview(head_abs)

        # ── 供电 ───────────────────────────────────────────────
        ps = self.power_supply.status
        ps_color = {"供电正常": "#16a34a", "电压低": "#d97706",
                     "断电": "#dc2626", "故障恢复": "#f59e0b"}.get(ps.value, "#333")
        self.power_label.setText(f"供电: {ps.value}")
        self.power_label.setStyleSheet(f"color: {ps_color}; font-size: 17px; font-weight: 650;")

        # ── 车门 ───────────────────────────────────────────────
        if ctrl.left_door_open:
            door_text = "左门开"
        elif ctrl.right_door_open:
            door_text = "右门开"
        else:
            door_text = "全部关闭"
        self.door_label.setText(f"车门: {door_text}")

        # ── 车站信息 ───────────────────────────────────────────
        station = self.track.get_station_at(head_abs)
        self.station_label.setText(f"当前站: {station.name if station else '区间'}")

        station_candidates = [
            station for station in self.track.stations
            if direction * (station.position - head_abs) > 5.0
        ]
        next_station = min(
            station_candidates,
            key=lambda station: direction * (station.position - head_abs),
            default=None,
        )
        if next_station:
            dist = direction * (next_station.position - head_abs)
            self.next_station_label.setText(f"下一站: {next_station.name}")
            self.distance_label.setText(f"下一站距离: {dist:.0f} m")
        else:
            self.next_station_label.setText("下一站: 终点")
            self.distance_label.setText("下一站距离: --")

        # ── 载荷等级 ───────────────────────────────────────────
        self.load_label.setText(f"载荷: AW{self._current_load_level().value}")

        # ── 编组信息 ───────────────────────────────────────────
        self.consist_label.setText(self._consist_text())

        # ── 进度条 ─────────────────────────────────────────────
        total = self.track.total_length()
        if total > 0:
            progress = int(head_abs / total * 1000)
            self.progress_bar.setValue(min(progress, 1000))
            self._refresh_station_markers(total)

    def _current_load_level(self) -> LoadLevel:
        """根据头车质量推断当前载荷等级。"""
        if not self.controller.consist or len(self.controller.consist) == 0:
            return LoadLevel.AW2
        car = self.controller.consist[0]
        mass = car.mass
        if mass <= car.aw0_mass * 1.05:
            return LoadLevel.AW0
        elif mass <= car.aw1_mass * 1.05:
            return LoadLevel.AW1
        elif mass <= car.aw2_mass * 1.05:
            return LoadLevel.AW2
        return LoadLevel.AW3

    def _consist_text(self) -> str:
        c = self.controller.consist
        if c is None:
            return ""
        return f"{c.motor_count}M{c.trailer_count}T  |  {c.total_mass / 1000:.0f}t  |  {len(c)}节"

    # ── 信号序列 ──────────────────────────────────────────────

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
        if aspect == SignalAspect.RED:
            return "#dc2626"
        if aspect == SignalAspect.YELLOW:
            return "#d97706"
        return "#16a34a"

    def _refresh_station_markers(self, total_length: float):
        names = [f"{station.name} {station.position / total_length * 100:.0f}%" for station in self.track.stations]
        self.station_markers.setText("  |  ".join(names))
