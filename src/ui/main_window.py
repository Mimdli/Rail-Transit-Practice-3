"""主窗口 — 应用程序主界面"""

from PyQt5.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QGroupBox, QTextEdit
from PyQt5.QtCore import QTimer

from src.ui.dashboard import Dashboard
from src.ui.controls import ControlPanel
from src.vehicle.model import VehicleModel, RunningMode
from src.vehicle.controller import ManualController, AutoController
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
        self.setMinimumSize(1280, 760)

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
        self.vehicle = VehicleModel()
        self.manual_ctrl = ManualController(self.vehicle)
        self.auto_ctrl = AutoController(self.vehicle)

        loader = TrackLoader()
        self.track = loader.load_demo_data()

        self.signal_system = SignalSystem()
        self.power_supply = PowerSupply()
        self.interlock = DoorInterlock(self.vehicle, self.track)
        self.recorder = Recorder()
        self.evaluator = Evaluator()

        self.front_train_positions: list[float] = [300.0]
        self._last_signal_aspects: dict[str, SignalAspect] = {}
        self._last_status_log_time: float = -1.0
        self._displayed_event_count: int = 0
        self.sim_time: float = 0.0
        self.recorder.record("系统", "系统启动，演示前车位置: 300 m")

    def _init_ui(self):
        """初始化界面"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(0)

        self.dashboard = Dashboard(self.vehicle, self.track, self.signal_system, self.power_supply)
        self.control_panel = ControlPanel(
            self.manual_ctrl, self.auto_ctrl, self.interlock, self.recorder, show_log=False
        )

        top_layout.addWidget(self.dashboard, stretch=4)
        top_layout.addWidget(self.control_panel, stretch=2)
        layout.addLayout(top_layout, stretch=5)
        layout.addWidget(self._create_log_panel(), stretch=2)

    def _update(self):
        """定时更新"""
        dt = self.vehicle.dt

        # 更新线路条件
        pos = self.vehicle.position
        self.vehicle.current_gradient = self.track.get_gradient_at(pos)
        track_limit = self.track.get_speed_limit_at(pos)

        # 根据信号机后方闭塞分区占用情况动态生成信号显示。
        self.signal_system.update_aspects_by_occupancy(
            self.track.signals, self.front_train_positions
        )
        self._record_signal_changes()

        # 信号限速
        self.vehicle.current_speed_limit = self.signal_system.get_effective_speed_limit(
            pos, track_limit, self.track.signals
        )

        # 供电状态影响牵引能力
        if not self.power_supply.can_traction():
            if self.vehicle.control_level.value > 0:
                self.vehicle.set_control_level_direct(self.vehicle.control_level)

        # 自动运行
        if self.vehicle.running_mode == RunningMode.AUTOMATIC:
            self.auto_ctrl.step()

        # 推进仿真
        self.vehicle.step()
        self.power_supply.step(dt)
        self.recorder.step(dt)
        self.sim_time += dt

        # 评价
        self.evaluator.update_max_speed(self.vehicle.speed)
        self._record_status_snapshot()

        # 超速检测
        if self.vehicle.speed > self.vehicle.current_speed_limit + 0.5:
            self.recorder.record("超速", f"超速: {self.vehicle.get_speed_kmh():.1f} km/h", pos, self.vehicle.speed)

        # 更新 UI
        self.dashboard.refresh()
        self._update_log_display()

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
                font-size: 28px;
                font-weight: 800;
                padding: 4px 2px 2px 2px;
            }
            #statusIndicator, #infoPanel, #panelGroup {
                background: #ffffff;
                border: 1px solid #d8dee8;
                border-radius: 8px;
            }
            #statusIndicator {
                min-height: 122px;
            }
            #indicatorLabel {
                color: #475467;
                font-size: 15px;
                font-weight: 700;
            }
            #indicatorUnit {
                color: #667085;
                font-size: 13px;
            }
            #sectionTitle {
                color: #1d2939;
                font-size: 17px;
                font-weight: 800;
            }
            #infoLabel {
                color: #1d2939;
                font-size: 17px;
                font-weight: 650;
                padding: 6px 2px;
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
                font-size: 17px;
                font-weight: 800;
                margin-top: 14px;
                padding: 16px 10px 10px 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
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
                padding: 18px 16px 14px 16px;
                font-size: 17px;
                font-weight: 800;
                color: #1d2939;
            }
        """)

    def _create_log_panel(self) -> QGroupBox:
        """创建横跨底部的运行日志区域"""
        log_group = QGroupBox("运行日志")
        log_group.setObjectName("bottomLogGroup")
        log_layout = QVBoxLayout(log_group)
        log_layout.setContentsMargins(16, 16, 16, 14)
        log_layout.setSpacing(8)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setObjectName("logText")
        self.log_text.setMinimumHeight(220)
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
                    self.vehicle.speed,
                )
                self._last_signal_aspects[sig.signal_id] = aspect

    def _record_status_snapshot(self):
        """每秒记录一次运行状态快照，便于复盘速度和加速度变化"""
        if self._last_status_log_time >= 0 and self.sim_time - self._last_status_log_time < 1.0:
            return

        nearest_signal = self.signal_system.get_nearest_signal_ahead(
            self.vehicle.position, self.track.signals
        )
        if nearest_signal:
            aspect = self.signal_system.get_signal_aspect(nearest_signal)
            signal_text = f"{nearest_signal.signal_id} {aspect.value}"
        else:
            signal_text = "无前方信号"

        mode = "自动" if self.vehicle.running_mode == RunningMode.AUTOMATIC else "手动"
        description = (
            f"状态快照: 模式 {mode} · 加速度 {self.vehicle.acceleration:+.2f} m/s² · "
            f"限速 {self.vehicle.current_speed_limit * 3.6:.0f} km/h · "
            f"信号 {signal_text} · 供电 {self.power_supply.status.value}"
        )
        self.recorder.record("状态", description, self.vehicle.position, self.vehicle.speed)
        self._last_status_log_time = self.sim_time
