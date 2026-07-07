"""控制面板 — TODO: 实现列车操作按钮与日志显示"""

from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel


class ControlPanel(QWidget):
    """控制面板 — 驾驶操作按钮和日志"""

    def __init__(self, manual_ctrl, auto_ctrl, interlock, recorder):
        super().__init__()
        self.manual_ctrl = manual_ctrl
        self.auto_ctrl = auto_ctrl
        self.interlock = interlock
        self.recorder = recorder
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("控制面板（待实现）"))

    def update_log(self, recorder):
        """更新日志显示"""
        # TODO: 显示最新的日志条目
        pass
