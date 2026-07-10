"""主窗口 — 应用程序主界面"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGroupBox, QTextEdit,
    QLabel, QComboBox, QTabWidget, QSplitter, QPushButton, QButtonGroup,
    QApplication,
)
from PyQt5.QtCore import QTimer, Qt

from src.ui.dashboard import Dashboard
from src.ui.controls import ControlPanel
from src.ui.track_view import TrackViewWidget
from src.ui.charts import ForcePanel
from src.vehicle.vehicle_controller import VehicleController
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.route_manager import RouteManager
from src.vehicle.enums import RunningMode, DoorSide, LoadLevel, StationPhase, ControlLevel
from src.vehicle.environment import MockEnvironment, WeatherType
from src.common.consist import CONSIST_4M2T
from src.track.adapter import TrackDataAdapter
from src.track.db_loader import DBLoader
from src.track.loader import TrackLoader
from src.signal.system import SignalSystem, SignalAspect
from src.power.supply import PowerSupply, PowerStatus
from src.door.interlock import DoorInterlock
from src.logger.recorder import Recorder
from src.logger.evaluator import Evaluator


class MainWindow(QMainWindow):
    """轨道交通模拟系统主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("轨道交通模拟系统")
        self.setMinimumSize(1400, 1080)
        self.resize(1400, 1080)

        # 初始化核心模块
        self._init_modules()

        # 初始化 UI
        self._init_ui()
        self._apply_style()

        # 定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self._update)
        self.timer.start(100)  # 100ms 刷新

    def _init_modules(self):
        """初始化所有核心模块"""
        # ── 线路数据 ─────────────────────────────────────────────
        self.data_mode = "demo"
        self.track = self._load_track_data(self.data_mode)
        self.track_adapter = TrackDataAdapter(self.track)

        # ── 环境（Mock，后续集成时替换） ──────────────────────────
        self.env = MockEnvironment(WeatherType.DRY, self.track_adapter)

        # ── 新版多体动力学控制器 ──────────────────────────────────
        self.controller = VehicleController(
            consist=CONSIST_4M2T,
            track=self.track_adapter,
            env=self.env,
        )
        # ── 联锁（需在 route_manager / auto_drive 之前创建） ────
        self.interlock = DoorInterlock(self.controller, self.track, self.track_adapter)

        # ── 进路管理器（独立于驾驶模式） ──────────────────────────
        self.route_manager = RouteManager(self.track_adapter)

        # ── 自动驾驶控制器 ────────────────────────────────────────
        self.auto_drive = AutoDriveController(
            self.controller, self.interlock,
            route_manager=self.route_manager,
        )

        # ── 进路（路线选择） ────────────────────────────────────
        self.routes = TrackLoader.create_demo_routes()
        self.route_manager.set_available_routes(self.routes)
        # 默认选中"自动"进路
        auto_route = next((r for r in self.routes if r.is_auto), self.routes[0])
        self.route_manager.set_route(auto_route)

        # ── 旧版兼容引用（供联锁/日志模块中未迁移的代码使用） ────
        self.vehicle = self.controller  # 兼容别名

        # ── 信号 / 供电 / 日志 ────────────────────────────────────
        self.signal_system = SignalSystem()
        self.power_supply = PowerSupply()
        self.recorder = Recorder()
        self.evaluator = Evaluator()

        self.front_train_positions: list[float] = [300.0]
        self._last_signal_aspects: dict[str, SignalAspect] = {}
        self._last_status_log_time: float = -1.0
        self._displayed_event_count: int = 0
        self.sim_time: float = 0.0

        # ── 时间加速 ────────────────────────────────────────────
        self.speed_multiplier: int = 1
        self.fast_forward_active: bool = False
        self.fast_forward_cancelled: bool = False

        self.recorder.record("系统", "系统启动，当前数据源: 演示数据")

    def _init_ui(self):
        """初始化界面"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._create_data_source_bar())
        layout.addWidget(self._create_speed_control_bar())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("mainTabs")
        layout.addWidget(self.tabs, stretch=1)

        sim_page = QWidget()
        sim_layout = QVBoxLayout(sim_page)
        sim_layout.setContentsMargins(0, 0, 0, 0)
        sim_layout.setSpacing(0)

        sim_splitter = QSplitter(Qt.Vertical)
        sim_splitter.setObjectName("simSplitter")

        self.dashboard = Dashboard(
            self.controller, self.track, self.track_adapter,
            self.signal_system, self.power_supply,
            auto_drive=self.auto_drive,
            route_manager=self.route_manager,
        )
        self.control_panel = ControlPanel(
            self.controller, self.auto_drive, self.interlock,
            self.track_adapter, self.recorder, show_log=False,
            route_manager=self.route_manager,
        )
        self.control_panel.populate_routes(self.routes)
        self.control_panel.populate_stations(self.track.stations)
        self.control_panel.consist_changed.connect(self._on_consist_changed)
        self.control_panel.mode_change_requested.connect(self.set_driving_mode)

        top_content = QWidget()
        top_layout = QHBoxLayout(top_content)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)
        top_layout.addWidget(self.dashboard, stretch=5)
        top_layout.addWidget(self.control_panel, stretch=2)

        sim_splitter.addWidget(top_content)
        sim_splitter.addWidget(self._create_log_panel())
        sim_splitter.setSizes([640, 180])
        sim_splitter.setCollapsible(0, False)
        sim_splitter.setCollapsible(1, True)
        sim_layout.addWidget(sim_splitter)

        self.track_view = TrackViewWidget(self.track)
        self.track_view.segment_clicked.connect(self._on_track_clicked)
        self.force_panel = ForcePanel()

        self.tabs.addTab(sim_page, "运行仿真")
        self.tabs.addTab(self.track_view, "线路可视化")
        self.tabs.addTab(self.force_panel, "力学分析")

    def _create_data_source_bar(self) -> QWidget:
        """创建数据源切换条"""
        bar = QWidget()
        bar.setObjectName("dataSourceBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        title = QLabel("线路数据源")
        title.setObjectName("dataSourceTitle")
        self.data_source_combo = QComboBox()
        self.data_source_combo.setObjectName("dataSourceCombo")
        self.data_source_combo.addItem("演示数据", "demo")
        self.data_source_combo.addItem("数据库线路", "database")
        self.data_source_combo.currentIndexChanged.connect(self._on_data_source_changed)

        self.data_source_status = QLabel(self._data_source_summary())
        self.data_source_status.setObjectName("dataSourceStatus")

        layout.addWidget(title)
        layout.addWidget(self.data_source_combo)
        layout.addWidget(self.data_source_status, stretch=1)
        return bar

    def _load_track_data(self, mode: str):
        """按模式加载线路数据"""
        if mode == "database":
            return DBLoader().load_from_db()
        return TrackLoader().load_demo_data()

    def _on_data_source_changed(self):
        """响应 UI 数据源切换"""
        mode = self.data_source_combo.currentData()
        if mode == self.data_mode:
            return

        try:
            new_track = self._load_track_data(mode)
        except Exception as exc:
            self.recorder.record("系统", f"切换数据源失败: {exc}",
                                 self._head_abs_position(), self.controller.head_speed)
            self.data_source_combo.blockSignals(True)
            self.data_source_combo.setCurrentIndex(self.data_source_combo.findData(self.data_mode))
            self.data_source_combo.blockSignals(False)
            return

        self.data_mode = mode
        self._replace_track(new_track)

    def _replace_track(self, track):
        """替换当前线路数据，并同步所有依赖该数据的模块"""
        self.track = track
        self.track_adapter = TrackDataAdapter(track)

        # 更新 controller 的线路和环境引用
        self.controller.track = self.track_adapter
        self.env.track = self.track_adapter
        self.route_manager.track_adapter = self.track_adapter
        # 找到 BFS 根区段（abs_start == 0），而非列表第一个
        start_seg_id = 1
        for seg in track.segments:
            if abs(seg.abs_start) < 0.01:
                start_seg_id = seg.seg_id
                break
        if start_seg_id == 1 and track.segments:
            start_seg_id = track.segments[0].seg_id
        self.controller.reset_states(start_segment_id=start_seg_id, start_offset=0.0)
        self.controller.set_running_mode(RunningMode.MANUAL)

        self.signal_system.clear_signal_aspects()
        self.interlock.track = track
        self.interlock.track_adapter = self.track_adapter
        self.dashboard.track = track
        self.dashboard.track_adapter = self.track_adapter

        # 重建车站下拉框
        self.control_panel.populate_stations(track.stations)

        # 重建线路可视化并立即将列车绘制到新线路上
        self.track_view.set_track_data(track, self._data_source_label())
        car_abs = [self.track_adapter.to_absolute(s.position) for s in self.controller.states]
        self.track_view.set_train_position(car_abs, self.controller)

        self.front_train_positions = self._default_front_train_positions()
        self._last_signal_aspects.clear()
        self._last_status_log_time = -1.0
        self.sim_time = 0.0
        self.power_supply.reset()
        self.data_source_status.setText(self._data_source_summary())
        self.recorder.record("系统", f"切换数据源: {self._data_source_label()}")
        # 重新连接点击导航信号（track_view 已被重建）
        self.track_view.segment_clicked.connect(self._on_track_clicked)
        self.dashboard.refresh()
        self.force_panel.clear()

    def _on_consist_changed(self, new_consist):
        """编组变更后更新列车可视化。"""
        car_abs = [self.track_adapter.to_absolute(s.position)
                   for s in self.controller.states]
        self.track_view.set_train_position(car_abs, self.controller)
        self.dashboard.refresh()
        self.recorder.record("系统",
                             f"编组变更为 {len(new_consist)} 车")

    def _default_front_train_positions(self) -> list[float]:
        """根据线路长度放置一个演示前车，供闭塞逻辑展示"""
        total = self.track.total_length()
        if total <= 0:
            return []
        return [min(300.0, total * 0.25)]

    def _data_source_label(self) -> str:
        """当前数据源展示名称"""
        return "数据库线路" if self.data_mode == "database" else "演示数据"

    def _data_source_summary(self) -> str:
        """当前数据源摘要"""
        if not hasattr(self, "track"):
            return ""
        return (
            f"{self._data_source_label()} | "
            f"区段 {len(self.track.segments)} | "
            f"车站 {len(self.track.stations)} | "
            f"信号 {len(self.track.signals)} | "
            f"总长 {self.track.total_length():.0f} m"
        )

    # ── 速度控制栏 ──────────────────────────────────────────────

    def _create_speed_control_bar(self) -> QWidget:
        """创建仿真速度控制栏（时间加速 + 极速跳站）。"""
        bar = QWidget()
        bar.setObjectName("speedControlBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(4)

        # 标题
        title = QLabel("仿真速度")
        title.setObjectName("speedSectionLabel")
        layout.addWidget(title)

        # 速度按钮组（互斥）
        self._speed_button_group = QButtonGroup(self)
        self._speed_button_group.setExclusive(True)
        for mult in (1, 2, 5, 10, 20):
            btn = QPushButton(f"{mult}x")
            btn.setObjectName("speedButton")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, m=mult: self._on_speed_multiplier_changed(m))
            self._speed_button_group.addButton(btn, mult)
            layout.addWidget(btn)
            if mult == 1:
                btn.setChecked(True)

        # 分隔线
        sep = QLabel("│")
        sep.setObjectName("speedSeparator")
        layout.addWidget(sep)

        # 跳到下一站
        self._fast_forward_button = QPushButton("跳到下一站")
        self._fast_forward_button.setObjectName("speedBarPrimary")
        self._fast_forward_button.clicked.connect(self._on_fast_forward)
        layout.addWidget(self._fast_forward_button)

        # 取消按钮（默认隐藏）
        self._cancel_button = QPushButton("取消")
        self._cancel_button.setObjectName("speedBarDanger")
        self._cancel_button.clicked.connect(self._on_cancel_fast_forward)
        self._cancel_button.setVisible(False)
        layout.addWidget(self._cancel_button)

        # 状态标签
        self._speed_status_label = QLabel("")
        self._speed_status_label.setObjectName("speedStatusLabel")
        layout.addWidget(self._speed_status_label)

        layout.addStretch()
        return bar

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _head_abs_position(self) -> float:
        """获取头车在线路上的绝对位置 (m)，供信号/日志等旧接口使用。"""
        states = self.controller.states
        if not states:
            return 0.0
        return self.track_adapter.to_absolute(states[0].position)

    def _head_gradient(self) -> float:
        """获取头车当前位置的坡度 (‰)。"""
        abs_pos = self._head_abs_position()
        return self.track.get_gradient_at(abs_pos)

    def _head_track_limit(self) -> float:
        """获取头车当前位置的线路限速 (m/s)。"""
        abs_pos = self._head_abs_position()
        return self.track.get_speed_limit_at(abs_pos)

    # ── 主更新循环 ──────────────────────────────────────────────

    def _update(self):
        """定时更新 — 支持时间加速（Path C）和极速跳站（Path D）。"""
        dt = 0.033  # 30fps 仿真步长（不变）

        # ── 网络模式：从外部系统注入数据 ──────────────────────
        if getattr(self, '_network_mode', False):
            self._network_update_step(dt)
            self.dashboard.refresh()
            self._update_log_display()
            return

        # ── 确定批处理大小 ────────────────────────────────────
        if self.fast_forward_active:
            batch_size = 500
        else:
            batch_size = self.speed_multiplier

        report = None
        head_speed = 0.0
        track_limit = 0.0
        effective_limit = 0.0

        for step_i in range(batch_size):
            # ── 快进取消检查 ──────────────────────────────────
            if self.fast_forward_cancelled:
                self.fast_forward_active = False
                self.fast_forward_cancelled = False
                self._cancel_button.setVisible(False)
                self._fast_forward_button.setEnabled(True)
                self.recorder.record(
                    "操作", "取消快进",
                    self._head_abs_position(), self.controller.head_speed,
                )
                break

            # ── 线路条件（头车绝对位置） ──────────────────────────
            head_abs = self._head_abs_position()
            head_speed = self.controller.head_speed

            # ── 信号系统 ──────────────────────────────────────────
            self.signal_system.update_aspects_by_occupancy(
                self.track.signals, self.front_train_positions
            )
            self._record_signal_changes()

            # 信号限速
            track_limit = self.track.get_speed_limit_at(head_abs)
            effective_limit = self.signal_system.get_effective_speed_limit(
                head_abs, track_limit, self.track.signals
            )

            # ── 供电状态影响牵引能力（系统级安全切断，旁路模式门禁）─
            if not self.power_supply.can_traction():
                self.controller.traction.set_throttle(0.0, bypass_mode_check=True)

            # ── 自动驾驶 ──────────────────────────────────────────
            if self.controller.running_mode == RunningMode.AUTOMATIC:
                self.auto_drive.step(dt)

            # ── 推进多体动力学仿真 ────────────────────────────────
            report = self.controller.step(dt)
            self.power_supply.step(dt)
            self.recorder.step(dt)
            self.sim_time += dt

            # ── 运行评价 ──────────────────────────────────────────
            self.evaluator.update_max_speed(head_speed)
            self._record_status_snapshot(effective_limit)

            # ── 超速检测 ──────────────────────────────────────────
            if head_speed > effective_limit + 0.5:
                self.recorder.record(
                    "超速",
                    f"超速: {head_speed * 3.6:.1f} km/h",
                    head_abs, head_speed,
                )

            # ── Path D 停止条件 ──────────────────────────────────
            if self.fast_forward_active and self._check_fast_forward_stop():
                self.fast_forward_active = False
                self._cancel_button.setVisible(False)
                self._fast_forward_button.setEnabled(True)
                break

            # ── UI 响应性（Path D 专用） ──────────────────────────
            if self.fast_forward_active and step_i % 50 == 0:
                QApplication.processEvents()

        # ── UI 刷新（每定时器周期一次） ────────────────────────
        if report is not None:
            self.dashboard.refresh(report)
            car_abs = [self.track_adapter.to_absolute(s.position)
                       for s in self.controller.states]
            self.track_view.set_train_position(car_abs, self.controller)
            self._update_log_display()

            # ── 更新力学分析面板 ───────────────────────────────────
            self.force_panel.feed(
                self.sim_time,
                head_speed * 3.6,
                report.max_coupler_force / 1000,
                track_limit * 3.6,
            )
            self.force_panel.set_report(report)

        # ── 更新速度状态标签 ──────────────────────────────────
        self._update_speed_status()

    # ── 时间加速控制 ─────────────────────────────────────────────

    def _on_speed_multiplier_changed(self, multiplier: int):
        """Path C：切换正常倍速。

        快进中切换倍速会自动退出快进模式。
        """
        self.speed_multiplier = multiplier
        if self.fast_forward_active:
            self.fast_forward_active = False
            self.fast_forward_cancelled = False
            self._cancel_button.setVisible(False)
            self._fast_forward_button.setEnabled(True)
            self.recorder.record(
                "操作", f"切换至 {multiplier}x，退出快进",
                self._head_abs_position(), self.controller.head_speed,
            )

    def set_driving_mode(self, mode: RunningMode):
        """统一驾驶模式切换入口。

        处理模式切换的所有副作用：
        - 关门（安全）
        - MANUAL → 重置 ATO 状态、施加常用制动、同步手柄
        - AUTOMATIC → 查找下一站、设目标
        - UI 反馈（模式显示更新 + 过渡提示）

        由 ControlPanel.mode_change_requested 信号和 _on_fast_forward 调用。

        Args:
            mode: 目标驾驶模式。
        """
        if self.controller.running_mode == mode:
            return  # 已在目标模式

        # 先关门（安全：不允许门开着切换模式）
        self.controller.close_door()

        if mode == RunningMode.MANUAL:
            self.auto_drive.reset_state()
            self.controller.set_running_mode(RunningMode.MANUAL)
            self.controller.command_stop(brake_level=0.4)
            self.recorder.record("操作", "切换手动模式",
                                 self._head_abs_position(), self.controller.head_speed)
            # UI 反馈
            self.control_panel._update_mode_display()
            self.control_panel._sync_handle_button(ControlLevel.SERVICE_BRAKE)
            self.control_panel._show_mode_transition(
                True, "✓ 已切换至手动驾驶 — 请操作司控器手柄")

        elif mode == RunningMode.AUTOMATIC:
            head_abs = self._head_abs_position()
            next_station = self.auto_drive.find_next_station(head_abs)
            if next_station is None:
                self.recorder.record("操作", "切换自动失败: 无前方车站",
                                     head_abs, self.controller.head_speed)
                self.control_panel._show_mode_transition(
                    False, "✗ 切换失败: 无前方车站")
                return
            target = self.track_adapter.from_absolute(next_station.position)
            self.auto_drive.set_target(target)
            self.controller.set_running_mode(RunningMode.AUTOMATIC)
            self.recorder.record("操作", f"切换自动模式，目标: {next_station.name}",
                                 head_abs, self.controller.head_speed)
            # UI 反馈
            self.control_panel._update_mode_display()
            self.control_panel._show_mode_transition(
                True, f"✓ 已切换至自动驾驶 → 目标: {next_station.name}")

    def _on_fast_forward(self):
        """Path D：极速跳站 —— 自动导航到下一个车站。"""
        head_abs = self._head_abs_position()

        # 查找前方车站
        next_station = self.track.get_nearest_station_ahead(head_abs)
        if next_station is None:
            self.recorder.record(
                "操作", "无前方车站可跳转",
                head_abs, self.controller.head_speed,
            )
            return

        # 如果已在自动驾驶中，记录目标覆盖警告
        if self.controller.running_mode == RunningMode.AUTOMATIC:
            old_target = self.auto_drive.target_position
            self.recorder.record(
                "操作",
                f"快进覆盖当前自动目标 → {next_station.name}",
                head_abs, self.controller.head_speed,
            )

        # 设置目标并切换到自动驾驶（通过统一入口）
        target = self.track_adapter.from_absolute(next_station.position)
        self.auto_drive.set_target(target)
        self.set_driving_mode(RunningMode.AUTOMATIC)

        self.fast_forward_active = True
        self.fast_forward_cancelled = False
        self._cancel_button.setVisible(True)
        self._fast_forward_button.setEnabled(False)

        self.recorder.record(
            "操作",
            f"快进至 {next_station.name} ({next_station.position:.0f}m)",
            head_abs, self.controller.head_speed,
        )

    def _on_cancel_fast_forward(self):
        """取消正在进行的极速跳站。"""
        self.fast_forward_cancelled = True

    def _check_fast_forward_stop(self) -> bool:
        """检查 Path D 停止条件。

        Returns:
            True 如果应停止快进。
        """
        head_abs = self._head_abs_position()
        head_speed = self.controller.head_speed
        distance = self.auto_drive.distance_to_target

        # 1. 到站进入停站阶段（优先判断，此时列车已精确停稳）
        if self.auto_drive.station_phase == StationPhase.DWELL:
            self.recorder.record(
                "操作", "已到站，自动开门",
                head_abs, head_speed,
            )
            return True

        # 2. 已到目标附近且停止
        if self.controller.is_stopped and distance is not None and distance < 5.0:
            self.recorder.record(
                "操作", "列车已停稳",
                head_abs, head_speed,
            )
            return True

        # 3. 严重超速（> 10.8 km/h over）
        track_limit = self.track.get_speed_limit_at(head_abs)
        effective_limit = self.signal_system.get_effective_speed_limit(
            head_abs, track_limit, self.track.signals,
        )
        if head_speed > effective_limit + 3.0:
            self.controller.emergency_brake()
            self.recorder.record(
                "超速",
                f"严重超速，快进中止: {head_speed * 3.6:.1f} km/h",
                head_abs, head_speed,
            )
            return True

        # 4. 线路终点
        total_len = self.track.total_length()
        if head_abs >= total_len - 10.0:
            self.controller.emergency_brake()
            self.recorder.record(
                "操作", "已达线路终点，快进中止",
                head_abs, head_speed,
            )
            return True

        # 5. 闯红灯
        for sig in self.track.signals:
            aspect = self.signal_system.get_signal_aspect(sig)
            if (aspect == SignalAspect.RED
                    and head_abs > sig.position
                    and head_abs - sig.position < 50):
                self.recorder.record(
                    "红灯违规",
                    f"闯红灯 {sig.signal_id}，快进中止",
                    head_abs, head_speed,
                )
                return True

        return False

    def _update_speed_status(self):
        """刷新速度控制栏的状态标签。"""
        if self.fast_forward_active:
            dist = self.auto_drive.distance_to_target
            if dist is not None:
                self._speed_status_label.setText(f"快进中… 距目标 {dist:.0f} m")
            else:
                self._speed_status_label.setText("快进中…")
        else:
            self._speed_status_label.setText(
                f"当前 {self.speed_multiplier}x  |  sim {self.sim_time:.1f}s"
            )

    # ── 网络通信（外部系统模式） ────────────────────────────────

    def _network_update_step(self, dt: float):
        """网络模式下的仿真步进"""
        head_abs = self._head_abs_position()
        head_speed = self.controller.head_speed
        head_accel = self.controller.states[0].acceleration if self.controller.states else 0.0
        curr_station = self._find_current_station(head_abs)

        # 1. 车辆UDP：发送本车状态 → 总控
        self.network.set_vehicle_send_source(lambda: [
            (head_accel, head_speed, head_abs)
        ] * 20)

        # 2. 车辆UDP：读取外部平台指令
        cmds = self.network.vehicle_commands
        if cmds and len(cmds) > 0:
            cmd, pct = cmds[0]
            if cmd == 1:
                self.controller.traction.set_throttle(float(pct) / 100.0, bypass_mode_check=True)
            elif cmd == 2:
                self.controller.traction.set_brake(float(pct) / 100.0, bypass_mode_check=True)
            elif cmd == 0:
                self.controller.traction.set_throttle(0.0, bypass_mode_check=True)

        # 3. 信号网关：发送信号/道岔状态
        switches = []
        signals = [(s.signal_id, self._aspect_to_protocol(
            self.signal_system.get_signal_aspect(s))) for s in self.track.signals]
        self.network.set_signal_send_source(lambda: (switches, signals[:20]))

        # 4. 视景系统：发送 TCMS2VIEW
        self._feed_vision_data(head_abs, head_speed, head_accel, signals)

        # 5. 司机台显示屏：发送网络屏 + 信号屏数据
        self._feed_cab_display(head_abs, head_speed, head_accel, curr_station)

        # 6. 推进本地仿真
        self.controller.step(dt)
        self.recorder.step(dt)
        self.sim_time += dt

        # 7. 刷新连接状态
        self._refresh_connection_status()

    def _feed_vision_data(self, head_abs: float, head_speed: float,
                          head_accel: float, signal_aspects: list):
        """为视景系统准备 TCMS2VIEW 数据"""
        sig_states = [s[1] for s in signal_aspects]
        sw_states = [0x01] * min(self.track.switches_count if hasattr(self.track, 'switches_count') else 0, 29)
        speed_mms = int(head_speed * 1000)                     # m/s → mm/s
        accel_pct = min(100, max(0, int(abs(head_accel) / 1.1 * 100)))
        run_state = 0x11 if head_accel > 0.01 else (0x12 if head_accel < -0.01 else 0x13)
        pos_mm = int(head_abs * 1000)

        def _vision_source():
            return {
                "signal_states": sig_states,
                "switch_states": sw_states,
                "speed_mms": speed_mms,
                "accel_pct": accel_pct,
                "run_state": run_state,
                "position_mm": pos_mm,
                "edge_id": self._abs_to_edge_id(head_abs),
                "direction": self._get_run_dir(),
                "other_trains": self._get_other_trains(),
            }

        self.network.set_vision_data_source(_vision_source)

    def _feed_cab_display(self, head_abs: float, head_speed: float,
                          head_accel: float, curr_station: int):
        """为司机台显示屏准备数据"""
        track_limit = self.track.get_speed_limit_at(head_abs) if hasattr(self, 'track') else 0
        effective_limit = self.signal_system.get_effective_speed_limit(
            head_abs, track_limit, self.track.signals) if hasattr(self, 'track') else track_limit

        net_data = {
            "speed": head_speed,
            "acceleration": head_accel,
            "speed_limit": effective_limit,
            "run_mode": self.controller.running_mode.value if self.controller.running_mode else 0,
            "run_dir": self._get_run_dir(),
            "power_pull": 80 if self.controller.throttle > 0 else 0,
            "net_pressure": 750 if self.power_supply.can_traction() else 0,
            "curr_station": curr_station,
            "next_station": self._find_next_station(head_abs),
            "end_station": curr_station,
            "power_state": self.power_supply.status.value if hasattr(self.power_supply, 'status') else 0,
            "door_states": self._get_door_states(),
            "has_power": self.power_supply.can_traction(),
        }

        sig_data = {
            "speed": head_speed,
            "acceleration": head_accel,
            "speed_limit": effective_limit,
            "mode": 5,  # RM mode default
            "run_dir": self._get_run_dir(),
            "curr_station": curr_station,
            "next_station": self._find_next_station(head_abs),
            "end_station": curr_station,
            "pull_switch": 1 if self.controller.throttle > 0 else 0,
            "pull_state": self.controller.traction_level if hasattr(self.controller, 'traction_level') else 0,
            "brake_state": self.controller.brake_level if hasattr(self.controller, 'brake_level') else 0,
            "urgency_stop": 1 if self.controller.emergency_brake else 0,
            "event_id": 0,
            "sig_state": self._get_front_signal_state(),
            "train_no": 1,
            "next_station_dist": self._distance_to_next_station(head_abs),
        }

        self.network.set_cab_network_source(lambda: net_data)
        self.network.set_cab_signal_source(lambda: sig_data)

    def _aspect_to_protocol(self, aspect) -> int:
        """将内部 SignalAspect 映射为协议灯色码"""
        from src.signal.system import SignalAspect
        mapping = {
            SignalAspect.RED: 0x01,
            SignalAspect.YELLOW: 0x02,
            SignalAspect.GREEN: 0x04,
        }
        return mapping.get(aspect, 0x00)

    def _get_run_dir(self) -> int:
        """获取运行方向: 0=上行, 1=下行"""
        return 0

    def _abs_to_edge_id(self, head_abs: float) -> int:
        """根据绝对位置获取边号（区段号）"""
        if hasattr(self, 'track') and hasattr(self.track, '_seg_map'):
            for sid, seg in self.track._seg_map.items():
                if seg.abs_start <= head_abs <= seg.abs_start + seg.length:
                    return sid
        return 0

    def _get_other_trains(self) -> list:
        """获取他车信息（当前仅演示数据）"""
        return []

    def _get_door_states(self) -> list[int]:
        """获取6节车门的开关状态"""
        states = []
        for i in range(6):
            state = 0  # 0=关门, 1=开门
            if hasattr(self, 'door_interlock'):
                if self.door_interlock.is_door_open():
                    state = 1
            states.append(state)
        return states

    def _find_current_station(self, head_abs: float) -> int:
        """查找当前所在车站ID"""
        if not hasattr(self, 'track'):
            return 0
        for st in self.track.stations:
            if abs(st.abs_pos - head_abs) < 50:
                return st.station_id if hasattr(st, 'station_id') else 0
        return 0

    def _find_next_station(self, head_abs: float) -> int:
        """查找下一站ID"""
        if not hasattr(self, 'track'):
            return 0
        next_id = 0
        for st in self.track.stations:
            if st.abs_pos > head_abs + 20:
                next_id = st.station_id if hasattr(st, 'station_id') else 0
                break
        return next_id

    def _distance_to_next_station(self, head_abs: float) -> float:
        """距下一站距离 (m)"""
        if not hasattr(self, 'track'):
            return 0.0
        for st in self.track.stations:
            if st.abs_pos > head_abs + 20:
                return st.abs_pos - head_abs
        return 0.0

    def _get_front_signal_state(self) -> int:
        """获取前方信号机状态"""
        return 0

    def _refresh_connection_status(self):
        """刷新连接状态显示"""
        if not self._network_mode:
            return
        self.data_source_status.setText(self._data_source_summary())

    def _setup_network_callbacks(self):
        """设置网络模块的回调"""
        def on_vehicle_recv(commands):
            pass
        def on_signal_recv(data: bytes):
            pass
        def on_plc_recv(data: dict):
            if "handle_position" in data and self._network_mode:
                handle = data.get("handle_position", 0)
                if handle > 0:
                    self.controller.traction.set_throttle(handle / 127.0, bypass_mode_check=True)
        self.network.set_vehicle_recv_callback(on_vehicle_recv)
        self.network.set_signal_recv_callback(on_signal_recv)
        self.network.set_plc_recv_callback(on_plc_recv)

    # ── 点击导航（调试模式） ──────────────────────────────────

    def _on_track_clicked(self, seg_id: int, abs_pos: float):
        """线路可视化区段点击 → 自动驾驶导航到目标位置。

        自动计算从头车当前位置到目标区段终点的进路，
        包括侧线段（查找预定义进路或沿主线+侧线组合算路）。

        Args:
            seg_id: 被点击的区段 ID。
            abs_pos: 点击位置对应的线路绝对坐标 (m)。
        """
        from src.track.route import Route, compute_mainline_route

        # 目标位置：点击区段的终点
        seg = self.track._seg_map.get(seg_id)
        if seg is None:
            return
        target_abs = seg.abs_start + seg.length
        target = self.track_adapter.from_absolute(target_abs)

        # 计算进路
        route = self._compute_route_to_segment(target.segment_id)
        if route is not None:
            self.route_manager.set_route(route)
            self.control_panel.route_combo.blockSignals(True)
            # 在下拉框中选中对应进路
            for i in range(self.control_panel.route_combo.count()):
                if self.control_panel.route_combo.itemText(i) == route.name:
                    self.control_panel.route_combo.setCurrentIndex(i)
                    break
            self.control_panel.route_combo.blockSignals(False)

        # 设置目标（不强制切换驾驶模式，由用户决定）
        self.auto_drive.set_target(target)
        # 在可视化上显示目标标记
        if self.track_view.view:
            self.track_view.view.set_target_marker(target_abs)
        seg_name = f"侧线{seg_id - 4}" if seg_id > 4 else f"主线 seg{seg_id}"
        self.recorder.record("操作",
            f"点击导航 → {seg_name} 终点 ({target_abs:.0f}m)",
            self._head_abs_position(), self.controller.head_speed)

    def _compute_route_to_segment(self, target_seg_id: int):
        """计算从头车到目标区段的进路。

        优先在预定义进路中查找，若无则尝试主线自动路由。
        对于侧线目标，查找包含该 seg_id 的预定义侧线进路。

        Returns:
            Route 或 None（找不到合适进路时使用自动模式）。
        """
        from src.track.route import Route

        if not self.controller.states:
            return None

        # 目标在主线（1-4）→ 自动算路
        if target_seg_id in (1, 2, 3, 4):
            return Route(0, "自动", [])

        # 目标在侧线 → 查找预定义进路
        for r in self.route_manager.available_routes:
            if r.is_auto:
                continue
            if r.last_seg_id == target_seg_id:
                return r

        # 无匹配进路 → 自动模式兜底
        return Route(0, "自动", [])

    def closeEvent(self, event):
        """关闭窗口"""
        self.timer.stop()
        event.accept()

    def _apply_style(self):
        """设置全局视觉样式，提升仪表盘可读性"""
        self.setStyleSheet("""
            QMainWindow {
                background: #eef2f6;
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
            }
            QWidget {
                color: #172033;
                font-size: 16px;
            }
            #pageTitle {
                color: #111827;
                font-size: 22px;
                font-weight: 800;
                padding: 2px 1px 1px 1px;
            }
            #statusIndicator, #infoPanel, #panelGroup {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 8px;
            }
            #dataSourceBar {
                background: #ffffff;
                border-bottom: 1px solid #d8dee8;
            }
            #dataSourceTitle {
                color: #1d2939;
                font-size: 16px;
                font-weight: 800;
            }
            #dataSourceStatus {
                color: #475467;
                font-size: 14px;
                font-weight: 650;
            }
            QComboBox#dataSourceCombo {
                min-width: 150px;
                min-height: 34px;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 4px 10px;
                background: #f8fafc;
                color: #172033;
                font-size: 15px;
                font-weight: 700;
            }
            QTabWidget::pane {
                border: 0;
                background: #eef2f6;
            }
            QTabBar::tab {
                background: #e5eaf2;
                color: #475467;
                padding: 11px 22px;
                margin-right: 2px;
                font-size: 15px;
                font-weight: 700;
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #172033;
            }
            QSplitter::handle {
                background: #d8dee8;
            }
            QSplitter::handle:vertical {
                height: 5px;
            }
            #trackViewWidget {
                background: #f8fafc;
            }
            #trackStatusBar {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 7px;
                color: #334155;
                font-size: 15px;
                font-weight: 700;
                padding: 10px 12px;
            }
            #statusIndicator {
                min-height: 80px;
            }
            #indicatorLabel {
                color: #475467;
                font-size: 13px;
                font-weight: 700;
            }
            #indicatorUnit {
                color: #667085;
                font-size: 11px;
            }
            #sectionTitle {
                color: #1d2939;
                font-size: 17px;
                font-weight: 800;
            }
            #infoLabel {
                color: #1d2939;
                font-size: 15px;
                font-weight: 650;
                padding: 5px 6px;
                min-height: 24px;
            }
            #signalOverview {
                color: #172033;
                font-size: 16px;
                line-height: 1.5;
                padding-top: 4px;
            }
            #stationMarkers {
                color: #475467;
                font-size: 14px;
                font-weight: 650;
            }
            QProgressBar {
                background: #e5eaf2;
                border: 0;
                border-radius: 10px;
            }
            QProgressBar::chunk {
                background: #2563eb;
                border-radius: 10px;
            }
            QGroupBox {
                color: #1d2939;
                font-size: 15px;
                font-weight: 800;
                margin-top: 10px;
                padding: 8px 6px 6px 6px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 3px;
            }
            /* ── 驾驶模式大块（在全局 QGroupBox 之后） ── */
            QGroupBox#modeGroup {
                margin-top: 0;
                background: #ffffff;
                border: 2px solid #cbd5e1;
                border-radius: 10px;
            }
            QGroupBox#modeGroup::title {
                font-size: 16px;
                color: #1d2939;
            }
            QLabel#modeIndicator {
                font-size: 20px;
                font-weight: 800;
            }
            QLabel#modeDesc {
                color: #64748b;
                font-size: 14px;
                font-weight: 500;
                padding: 0 2px;
            }
            QPushButton#modeToggleBtn {
                min-height: 48px;
                border-radius: 8px;
                border: 2px solid #6366f1;
                background: #eef2ff;
                color: #4338ca;
                font-size: 17px;
                font-weight: 800;
            }
            QPushButton#modeToggleBtn:hover {
                background: #e0e7ff;
                border-color: #4f46e5;
            }
            /* ── 司控器手柄按钮 ── */
            QPushButton#tractionHandleBtn {
                min-height: 48px;
                min-width: 52px;
                border-radius: 6px;
                border: 2px solid #86efac;
                background: #f0fdf4;
                color: #166534;
                font-size: 13px;
                font-weight: 700;
                padding: 2px 4px;
            }
            QPushButton#tractionHandleBtn:checked {
                background: #16a34a;
                color: #ffffff;
                border-color: #16a34a;
            }
            QPushButton#tractionHandleBtn:hover:!checked {
                background: #dcfce7;
                border-color: #4ade80;
            }
            QPushButton#coastHandleBtn {
                min-height: 48px;
                min-width: 52px;
                border-radius: 6px;
                border: 2px solid #cbd5e1;
                background: #f8fafc;
                color: #475569;
                font-size: 13px;
                font-weight: 700;
                padding: 2px 4px;
            }
            QPushButton#coastHandleBtn:checked {
                background: #64748b;
                color: #ffffff;
                border-color: #64748b;
            }
            QPushButton#coastHandleBtn:hover:!checked {
                background: #f1f5f9;
                border-color: #94a3b8;
            }
            QPushButton#brakeHandleBtn {
                min-height: 48px;
                min-width: 52px;
                border-radius: 6px;
                border: 2px solid #fca5a5;
                background: #fef2f2;
                color: #991b1b;
                font-size: 13px;
                font-weight: 700;
                padding: 2px 4px;
            }
            QPushButton#brakeHandleBtn:checked {
                background: #dc2626;
                color: #ffffff;
                border-color: #dc2626;
            }
            QPushButton#brakeHandleBtn:hover:!checked {
                background: #fee2e2;
                border-color: #f87171;
            }
            /* ── 手柄百分比条 ── */
            QFrame#handleBarFrame {
                background: transparent;
                border: 0;
                margin: 0;
                padding: 0;
            }
            QProgressBar#brakeBar {
                background: #f1f5f9;
                border: 1px solid #e2e8f0;
                border-radius: 4px;
                margin: 0;
            }
            QProgressBar#brakeBar::chunk {
                background: qlineargradient(x1:1, x2:0, stop:0 #dc2626, stop:1 #fca5a5);
                border-radius: 3px;
            }
            QProgressBar#tractionBar {
                background: #f1f5f9;
                border: 1px solid #e2e8f0;
                border-radius: 4px;
                margin: 0;
            }
            QProgressBar#tractionBar::chunk {
                background: qlineargradient(x1:0, x2:1, stop:0 #86efac, stop:1 #16a34a);
                border-radius: 3px;
            }
            QLabel#handlePctLabel {
                font-size: 16px;
                font-weight: 800;
                color: #64748b;
                padding: 2px 4px;
            }
            QLabel#handleLevelLabel {
                color: #334155;
                font-size: 14px;
                font-weight: 700;
                padding: 4px 8px;
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 5px;
            }
            QPushButton {
                min-height: 44px;
                border-radius: 7px;
                border: 1px solid #cbd5e1;
                background: #ffffff;
                color: #172033;
                font-size: 16px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #f4f7fb;
                border-color: #94a3b8;
            }
            QPushButton#primaryButton {
                background: #2563eb;
                border-color: #2563eb;
                color: #ffffff;
            }
            QPushButton#warningButton {
                background: #f59e0b;
                border-color: #f59e0b;
                color: #111827;
            }
            QPushButton#dangerButton {
                background: #dc2626;
                border-color: #dc2626;
                color: #ffffff;
            }
            /* ── 速度控制栏（在全局 QPushButton 之后，确保覆盖） ── */
            #speedControlBar {
                background: #ffffff;
                border-bottom: 1px solid #d8dee8;
            }
            #speedSectionLabel {
                color: #1d2939;
                font-size: 16px;
                font-weight: 800;
                margin-right: 12px;
            }
            QPushButton#speedButton {
                min-height: 30px;
                min-width: 40px;
                max-width: 52px;
                border-radius: 5px;
                border: 1px solid #cbd5e1;
                background: #f8fafc;
                color: #475467;
                font-size: 14px;
                font-weight: 700;
                padding: 3px 6px;
                margin: 0 2px;
            }
            QPushButton#speedButton:checked {
                background: #2563eb;
                color: #ffffff;
                border-color: #2563eb;
            }
            QPushButton#speedButton:hover:!checked {
                background: #eef2f6;
                border-color: #94a3b8;
            }
            QPushButton#speedBarPrimary {
                min-height: 30px;
                border-radius: 5px;
                border: 1px solid #2563eb;
                background: #2563eb;
                color: #ffffff;
                font-size: 14px;
                font-weight: 700;
                padding: 3px 10px;
                margin: 0 2px;
            }
            QPushButton#speedBarPrimary:hover {
                background: #1d4ed8;
            }
            QPushButton#speedBarDanger {
                min-height: 30px;
                border-radius: 5px;
                border: 1px solid #dc2626;
                background: #dc2626;
                color: #ffffff;
                font-size: 14px;
                font-weight: 700;
                padding: 3px 10px;
                margin: 0 2px;
            }
            QPushButton#speedBarDanger:hover {
                background: #b91c1c;
            }
            #speedSeparator {
                color: #cbd5e1;
                font-size: 18px;
                margin: 0 8px;
            }
            #speedStatusLabel {
                color: #475467;
                font-size: 14px;
                font-weight: 650;
            }
            #statusHint {
                background: #fff7ed;
                border: 1px solid #fed7aa;
                border-radius: 7px;
                color: #9a3412;
                padding: 10px 12px;
                font-size: 16px;
                font-weight: 700;
            }
            /* ── 控制面板紧凑按钮（在全局 QPushButton 之后） ── */
            QPushButton#smallButton {
                min-height: 26px;
                min-width: 34px;
                max-width: 44px;
                border-radius: 4px;
                border: 1px solid #cbd5e1;
                background: #f8fafc;
                color: #334155;
                font-size: 12px;
                font-weight: 700;
                padding: 1px 3px;
                margin: 1px;
            }
            QPushButton#smallButton:checked {
                background: #2563eb;
                color: #ffffff;
                border-color: #2563eb;
            }
            QPushButton#smallButton:hover:!checked {
                background: #eef2f6;
                border-color: #94a3b8;
            }
            QTextEdit#logText {
                background: #111827;
                color: #e5e7eb;
                border: 1px solid #1e293b;
                border-radius: 8px;
                font-family: Consolas, "Microsoft YaHei UI", monospace;
                font-size: 14px;
                padding: 12px;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
            }
            #bottomLogGroup {
                background: #ffffff;
                border-top: 1px solid #d8dee8;
                border-left: 0;
                border-right: 0;
                border-bottom: 0;
                border-radius: 0;
                margin: 0;
                padding: 14px 16px 10px 16px;
                font-size: 17px;
                font-weight: 800;
                color: #1d2939;
            }
        """)

    def _create_log_panel(self) -> QGroupBox:
        """创建横跨底部的运行日志区域"""
        log_group = QGroupBox("运行日志")
        log_group.setObjectName("bottomLogGroup")
        log_group.setMinimumHeight(230)
        log_group.setMaximumHeight(320)
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(16, 14, 16, 12)
        log_layout.setSpacing(8)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("logText")
        self.log_text.setMinimumHeight(180)
        log_layout.addWidget(self.log_text)
        return log_group

    def _update_log_display(self):
        """刷新底部全宽日志，只追加新增事件"""
        for event in self.recorder.events[self._displayed_event_count:]:
            self.log_text.append(self._format_log_event(event))
        self._displayed_event_count = len(self.recorder.events)
        self.log_text.moveCursor(self.log_text.textCursor().End)

    def _format_log_event(self, event) -> str:
        """把事件格式化成清晰的分块日志"""
        color = self._event_color(event.event_type)
        meta = f"位置 {event.position:.1f} m · 速度 {event.speed * 3.6:.1f} km/h"
        description = event.description.replace("状态快照: ", "")
        return (
            "<div style='margin:0 0 8px 0;'>"
            f"<span style='color:#93c5fd;'>[{event.timestamp:5.1f}s]</span> "
            f"<span style='color:{color}; font-weight:700;'>● {event.event_type}</span> "
            f"<span style='color:#f8fafc;'>{description}</span>"
            f"<div style='color:#94a3b8; font-size:12px; margin-top:1px;'>{meta}</div>"
            "</div>"
        )

    def _event_color(self, event_type: str) -> str:
        """按事件类型区分日志颜色"""
        if event_type in ("紧急制动", "超速", "红灯违规"):
            return "#f87171"
        if event_type == "信号":
            return "#fbbf24"
        if event_type == "状态":
            return "#38bdf8"
        if event_type == "操作":
            return "#86efac"
        return "#c4b5fd"

    def _record_signal_changes(self):
        """记录信号显示变化，避免日志中重复写入相同状态"""
        for sig in self.track.signals:
            aspect = self.signal_system.get_signal_aspect(sig)
            last_aspect = self._last_signal_aspects.get(sig.signal_id)
            if aspect != last_aspect:
                self.recorder.record(
                    "信号",
                    f"{sig.signal_id} {aspect.value}",
                    sig.position,
                    self.controller.head_speed,
                )
                self._last_signal_aspects[sig.signal_id] = aspect

    def _record_status_snapshot(self, effective_limit: float):
        """每秒记录一次运行状态快照，便于复盘速度和加速度变化"""
        if self._last_status_log_time >= 0 and self.sim_time - self._last_status_log_time < 1.0:
            return

        head_abs = self._head_abs_position()
        nearest_signal = self.signal_system.get_nearest_signal_ahead(
            head_abs, self.track.signals
        )
        if nearest_signal:
            aspect = self.signal_system.get_signal_aspect(nearest_signal)
            signal_text = f"{nearest_signal.signal_id} {aspect.value}"
        else:
            signal_text = "无前方信号"

        mode = "自动" if self.controller.running_mode == RunningMode.AUTOMATIC else "手动"
        head_accel = self.controller.states[0].acceleration if self.controller.states else 0.0
        description = (
            f"状态快照: 模式 {mode} · 加速度 {head_accel:+.2f} m/s² · "
            f"限速 {effective_limit * 3.6:.0f} km/h · "
            f"信号 {signal_text} · 供电 {self.power_supply.status.value}"
        )
        self.recorder.record("状态", description, head_abs, self.controller.head_speed)
        self._last_status_log_time = self.sim_time
