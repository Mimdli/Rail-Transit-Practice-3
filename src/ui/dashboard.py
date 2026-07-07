"""状态面板 — TODO: 实现列车运行状态实时显示"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel


class Dashboard(QWidget):
    """仪表盘 — 显示所有运行状态"""

    def __init__(self, vehicle, track, signal_system, power_supply):
        super().__init__()
        self.vehicle = vehicle
        self.track = track
        self.signal_system = signal_system
        self.power_supply = power_supply
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("状态面板（待实现）"))

    def refresh(self):
        """刷新所有显示"""
        # TODO: 更新速度、位置、限速、坡度、信号、供电、车门、车站等信息
        pass
