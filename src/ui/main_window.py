"""主窗口 — 应用程序主界面"""

from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGroupBox, QTextEdit,
    QLabel, QComboBox, QTabWidget, QSplitter
)
from PyQt5.QtCore import QTimer, Qt

from src.ui.dashboard import Dashboard
from src.ui.controls import ControlPanel
from src.ui.track_view import TrackViewWidget
from src.ui.charts import ForcePanel
from src.vehicle.vehicle_controller import VehicleController
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.enums import RunningMode, DoorSide, LoadLevel
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
from src.network import NetworkManager


class MainWindow(QMainWindow):
    """轨道交通模拟系统主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("轨道交通模拟系统")
        self.setMinimumSize(1400, 860)

        # 初始化核心模块
        self._init_modules()

        # 初始化 UI
        self._init_ui()
        self._apply_style()

        # 定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self._update)
        self._timer_ms = 100
        self._physics_substeps = 3  # 每帧多次积分，兼顾稳定性与实时性
        self.timer.start(self._timer_ms)

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
        self.auto_drive = AutoDriveController(self.controller)

        # ── 旧版兼容引用（供联锁/日志模块中未迁移的代码使用） ────
        self.vehicle = self.controller  # 兼容别名

        # ── 信号 / 供电 / 联锁 / 日志 ────────────────────────────
        self.signal_system = SignalSystem()
        self.power_supply = PowerSupply()
        self.interlock = DoorInterlock(self.controller, self.track, self.track_adapter)
        self.recorder = Recorder()
        self.evaluator = Evaluator()

        # ── 网络通信（可选，启动时不连接外部） ─────────────────
        self.network = NetworkManager()
        self._setup_network_callbacks()
        self._network_mode = False

        self.front_train_positions: list[float] = [300.0]
        self._last_signal_aspects: dict[str, SignalAspect] = {}
        self._last_status_log_time: float = -1.0
        self._displayed_event_count: int = 0
        self.sim_time: float = 0.0
        self.recorder.record("系统", "系统启动，当前数据源: 演示数据")

    def _init_ui(self):
        """初始化界面"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._create_data_source_bar())

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
        )
        self.control_panel = ControlPanel(
            self.controller, self.auto_drive, self.interlock,
            self.track_adapter, self.recorder, show_log=False,
        )

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
        self.data_source_combo.addItem("外部系统", "network")
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

        # 切换到外部系统模式
        if mode == "network":
            self._network_mode = True
            self.data_mode = "network"
            self.network.start()
            self.recorder.record("系统", "切换到外部系统模式，网络通信已启动")
            self.data_source_status.setText("外部系统 | 连接中...")
            self._refresh_connection_status()
            return

        # 从外部系统切回本地模式
        if self._network_mode:
            self._network_mode = False
            self.network.stop()

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
        # 找到 BFS 根区段（abs_start == 0），而非列表第一个
        start_seg_id = 1
        for seg in track.segments:
            if abs(seg.abs_start) < 0.01:
                start_seg_id = seg.seg_id
                break
        if start_seg_id == 1 and track.segments:
            start_seg_id = track.segments[0].seg_id
        self.controller.reset_states(start_segment_id=start_seg_id, start_offset=0.0)
        self.controller.running_mode = RunningMode.MANUAL

        self.signal_system.clear_signal_aspects()
        self.interlock.track = track
        self.interlock.track_adapter = self.track_adapter
        self.dashboard.track = track
        self.dashboard.track_adapter = self.track_adapter

        # 重建线路可视化并立即将列车绘制到新线路上
        self.track_view.set_track_data(track, self._data_source_label())
        car_abs = [self.track_adapter.to_absolute(s.position) for s in self.controller.states]
        car_segs = [s.position.segment_id for s in self.controller.states]
        self.track_view.set_train_position(car_abs, self.controller, car_segs)

        self.front_train_positions = self._default_front_train_positions()
        self._last_signal_aspects.clear()
        self._last_status_log_time = -1.0
        self.sim_time = 0.0
        self.power_supply.reset()
        self.data_source_status.setText(self._data_source_summary())
        self.recorder.record("系统", f"切换数据源: {self._data_source_label()}")
        self.dashboard.refresh()
        self.force_panel.clear()

    def _default_front_train_positions(self) -> list[float]:
        """根据线路长度放置一个演示前车，供闭塞逻辑展示"""
        total = self.track.total_length()
        if total <= 0:
            return []
        return [min(300.0, total * 0.25)]

    def _data_source_label(self) -> str:
        """当前数据源展示名称"""
        if self.data_mode == "database":
            return "数据库线路"
        if self.data_mode == "network":
            return "外部系统"
        return "演示数据"

    def _data_source_summary(self) -> str:
        """当前数据源摘要"""
        if self.data_mode == "network":
            status = self.network.connection_status
            parts = [self._data_source_label()]
            if status["vehicle_udp"]:
                parts.append("车辆UDP ✓")
            if status["signal_gateway"]:
                parts.append("信号网关 ✓")
            if status["plc"]:
                parts.append("PLC ✓")
            if not any(status.values()):
                parts.append("未连接")
            return " | ".join(parts)
        if not hasattr(self, "track"):
            return ""
        return (
            f"{self._data_source_label()} | "
            f"区段 {len(self.track.segments)} | "
            f"车站 {len(self.track.stations)} | "
            f"信号 {len(self.track.signals)} | "
            f"总长 {self.track.total_length():.0f} m"
        )

    # ── 辅助方法 ──────────────────────────────────────────────────

    def _head_abs_position(self) -> float:
        """获取头车在线路上的绝对位置 (m)，供信号/日志等旧接口使用。"""
        states = self.controller.states
        if not states:
            return 0.0
        return self.track_adapter.to_absolute(states[0].position)

    def _head_gradient(self) -> float:
        """获取头车当前位置的坡度 (‰)。"""
        states = self.controller.states
        if states:
            return self.track_adapter.get_gradient(states[0].position)
        return self.track.get_gradient_at(self._head_abs_position())

    def _head_track_limit(self) -> float:
        """获取头车当前位置的线路限速 (m/s)。"""
        abs_pos = self._head_abs_position()
        return self.track.get_speed_limit_at(abs_pos)

    # ── 主更新循环 ──────────────────────────────────────────────

    def _update(self):
        """定时更新"""
        # 仿真时间与 UI 刷新对齐（原 dt=0.033 仅 1/3 实时，体感极慢）
        dt_frame = self._timer_ms / 1000.0
        dt = dt_frame / self._physics_substeps

        # ---- 网络模式：从外部系统注入数据 ----
        if self._network_mode:
            self._network_update_step(dt_frame)
            self.dashboard.refresh()
            car_abs = [self.track_adapter.to_absolute(s.position) for s in self.controller.states]
            car_segs = [s.position.segment_id for s in self.controller.states]
            self.track_view.set_train_position(car_abs, self.controller, car_segs)
            self._update_log_display()
            return

        # ---- 本地模式：原有逻辑 ----
        head_abs = self._head_abs_position()
        head_speed = self.controller.head_speed

        self.signal_system.update_aspects_by_occupancy(
            self.track.signals, self.front_train_positions
        )
        self._record_signal_changes()

        track_limit = self.track.get_speed_limit_at(head_abs)
        effective_limit = self.signal_system.get_effective_speed_limit(
            head_abs, track_limit, self.track.signals
        )

        if not self.power_supply.can_traction():
            self.controller.set_throttle(0.0)

        if self.controller.running_mode == RunningMode.AUTOMATIC:
            self.auto_drive.step()

        report = None
        for _ in range(self._physics_substeps):
            report = self.controller.step(dt)
        self.power_supply.step(dt_frame)
        self.recorder.step(dt_frame)
        self.sim_time += dt_frame

        self.evaluator.update_max_speed(head_speed)
        self._record_status_snapshot(effective_limit)

        if head_speed > effective_limit + 0.5:
            self.recorder.record("超速",
                f"超速: {head_speed * 3.6:.1f} km/h", head_abs, head_speed)

        self.dashboard.refresh(report)
        car_abs = [self.track_adapter.to_absolute(s.position) for s in self.controller.states]
        car_segs = [s.position.segment_id for s in self.controller.states]
        self.track_view.set_train_position(car_abs, self.controller, car_segs)
        self._update_log_display()

        self.force_panel.feed(
            self.sim_time,
            head_speed * 3.6,
            report.max_coupler_force / 1000,
            track_limit * 3.6,
        )
        self.force_panel.set_report(report)

    def _network_update_step(self, dt: float):
        """网络模式下的仿真步进：使用外部数据驱动"""
        # 1. 发送本地车辆状态到外部平台
        head_speed = self.controller.head_speed
        head_accel = self.controller.states[0].acceleration if self.controller.states else 0.0
        head_pos = self._head_abs_position()
        self.network.set_vehicle_send_source(lambda: [
            (head_accel, head_speed, head_pos)
        ] * 20)

        # 2. 读取外部平台指令，通过旧版控制器接口设置
        cmds = self.network.vehicle_commands
        if cmds and len(cmds) > 0:
            cmd, pct = cmds[0]
            if cmd == 1:  # 加速
                self.controller.set_throttle(float(pct) / 100.0)
            elif cmd == 2:  # 减速
                self.controller.set_brake(float(pct) / 100.0)
            elif cmd == 0:  # 惰行
                self.controller.set_throttle(0.0)
                self.controller.set_brake(0.0)

        # 3. 发送信号状态到总控
        switches = []
        signals = [(s.signal_id, self._aspect_to_protocol(
            self.signal_system.get_signal_aspect(s))) for s in self.track.signals]
        self.network.set_signal_send_source(lambda: (switches, signals[:20]))

        # 4. 推进本地仿真
        self.controller.step(dt)
        self.recorder.step(dt)
        self.sim_time += dt

        # 5. 定期刷新连接状态
        self._refresh_connection_status()

    def _aspect_to_protocol(self, aspect) -> int:
        """将内部 SignalAspect 映射为协议灯色码"""
        mapping = {
            SignalAspect.RED: 0x01,
            SignalAspect.YELLOW: 0x02,
            SignalAspect.GREEN: 0x04,
        }
        return mapping.get(aspect, 0x00)

    def _refresh_connection_status(self):
        """刷新连接状态显示"""
        if not self._network_mode:
            return
        self.data_source_status.setText(self._data_source_summary())

    def _setup_network_callbacks(self):
        """设置网络模块的回调函数"""
        def on_vehicle_recv(commands):
            pass
        def on_signal_recv(data: bytes):
            pass
        def on_plc_recv(data: dict):
            if "handle_position" in data:
                pass
        self.network.set_vehicle_recv_callback(on_vehicle_recv)
        self.network.set_signal_recv_callback(on_signal_recv)
        self.network.set_plc_recv_callback(on_plc_recv)

    def closeEvent(self, event):
        """关闭窗口"""
        self.timer.stop()
        if self._network_mode:
            self.network.stop()
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
            #statusHint {
                background: #fff7ed;
                border: 1px solid #fed7aa;
                border-radius: 7px;
                color: #9a3412;
                padding: 10px 12px;
                font-size: 16px;
                font-weight: 700;
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
