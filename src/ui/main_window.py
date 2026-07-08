"""主窗口 — 轨道交通模拟系统主界面"""

from PyQt5.QtWidgets import QMainWindow, QWidget, QVBoxLayout
from PyQt5.QtCore import QTimer

from src.ui.track_view import TrackViewWidget


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
        self.timer.start(100)

    def _init_modules(self):
        """初始化所有核心模块"""
        # TODO: 实例化 VehicleModel, ManualController, AutoController,
        #       TrackLoader, TrackData, SignalSystem, PowerSupply,
        #       DoorInterlock, Recorder, Evaluator
        pass

    def _init_ui(self):
        """初始化界面"""
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)

        # 线路可视化视图
        self.track_view = TrackViewWidget(self)
        layout.addWidget(self.track_view)

    def _update(self):
        """定时更新"""
        # TODO: 更新线路条件、信号、供电状态，推进仿真，刷新 UI
        pass

    def closeEvent(self, event):
        """关闭窗口"""
        self.timer.stop()
        event.accept()
