"""主窗口 — 应用程序主界面"""

from PyQt5.QtWidgets import QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QSplitter
from PyQt5.QtCore import QTimer

from src.ui.dashboard import Dashboard
from src.ui.controls import ControlPanel
from src.vehicle.model import VehicleModel, RunningMode
from src.vehicle.controller import ManualController, AutoController
from src.track.loader import TrackLoader
from src.signal.system import SignalSystem
from src.power.supply import PowerSupply, PowerStatus
from src.door.interlock import DoorInterlock
from src.logger.recorder import Recorder
from src.logger.evaluator import Evaluator


class MainWindow(QMainWindow):
    """轨道交通模拟系统主窗口"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("轨道交通模拟系统")
        self.setMinimumSize(1000, 700)

        # 初始化核心模块
        self._init_modules()

        # 初始化 UI
        self._init_ui()

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

        self.sim_time: float = 0.0

    def _init_ui(self):
        """初始化界面"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)

        self.dashboard = Dashboard(self.vehicle, self.track, self.signal_system, self.power_supply)
        self.control_panel = ControlPanel(self.manual_ctrl, self.auto_ctrl, self.interlock, self.recorder)

        layout.addWidget(self.dashboard, stretch=3)
        layout.addWidget(self.control_panel, stretch=1)

    def _update(self):
        """定时更新"""
        dt = self.vehicle.dt

        # 更新线路条件
        pos = self.vehicle.position
        self.vehicle.current_gradient = self.track.get_gradient_at(pos)
        track_limit = self.track.get_speed_limit_at(pos)

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

        # 超速检测
        if self.vehicle.speed > self.vehicle.current_speed_limit + 0.5:
            self.recorder.record("超速", f"超速: {self.vehicle.get_speed_kmh():.1f} km/h", pos, self.vehicle.speed)

        # 更新 UI
        self.dashboard.refresh()
        self.control_panel.update_log(self.recorder)

    def closeEvent(self, event):
        """关闭窗口"""
        self.timer.stop()
        event.accept()
