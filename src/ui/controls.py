"""控制面板 — 列车操作按钮与日志显示"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QTextEdit, QGroupBox, QMessageBox
)
from PyQt5.QtCore import Qt

from src.vehicle.controller import ManualController, AutoController
from src.vehicle.model import RunningMode, DoorSide
from src.door.interlock import DoorInterlock
from src.logger.recorder import Recorder


class ControlPanel(QWidget):
    """控制面板 — 驾驶操作按钮和日志"""

    def __init__(self, manual_ctrl: ManualController, auto_ctrl: AutoController,
                 interlock: DoorInterlock, recorder: Recorder):
        super().__init__()
        self.manual_ctrl = manual_ctrl
        self.auto_ctrl = auto_ctrl
        self.interlock = interlock
        self.recorder = recorder
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # === 驾驶控制 ===
        drive_group = QGroupBox("驾驶控制")
        drive_layout = QVBoxLayout(drive_group)

        # 牵引
        btn_traction = QPushButton("牵引")
        btn_traction.clicked.connect(self._on_traction)
        drive_layout.addWidget(btn_traction)

        # 惰行
        btn_coast = QPushButton("惰行")
        btn_coast.clicked.connect(self._on_coast)
        drive_layout.addWidget(btn_coast)

        # 常用制动
        btn_brake = QPushButton("常用制动")
        btn_brake.clicked.connect(self._on_service_brake)
        drive_layout.addWidget(btn_brake)

        # 紧急制动
        btn_emergency = QPushButton("紧急制动")
        btn_emergency.setStyleSheet("background-color: #e74c3c; color: white; font-weight: bold;")
        btn_emergency.clicked.connect(self._on_emergency_brake)
        drive_layout.addWidget(btn_emergency)

        layout.addWidget(drive_group)

        # === 车门控制 ===
        door_group = QGroupBox("车门控制")
        door_layout = QHBoxLayout(door_group)

        btn_left_door = QPushButton("开左门")
        btn_left_door.clicked.connect(self._on_open_left_door)
        door_layout.addWidget(btn_left_door)

        btn_right_door = QPushButton("开右门")
        btn_right_door.clicked.connect(self._on_open_right_door)
        door_layout.addWidget(btn_right_door)

        btn_close_door = QPushButton("关门")
        btn_close_door.clicked.connect(self._on_close_door)
        door_layout.addWidget(btn_close_door)

        layout.addWidget(door_group)

        # === 模式切换 ===
        mode_group = QGroupBox("运行模式")
        mode_layout = QHBoxLayout(mode_group)

        btn_manual = QPushButton("手动")
        btn_manual.clicked.connect(self._on_manual_mode)
        mode_layout.addWidget(btn_manual)

        btn_auto = QPushButton("自动")
        btn_auto.clicked.connect(self._on_auto_mode)
        mode_layout.addWidget(btn_auto)

        layout.addWidget(mode_group)

        # === 操作提示 ===
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("font-size: 12px; color: #e67e22; padding: 4px;")
        layout.addWidget(self.status_label)

        # === 日志 ===
        log_group = QGroupBox("运行日志")
        log_layout = QVBoxLayout(log_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumHeight(200)
        self.log_text.setStyleSheet("font-size: 11px;")
        log_layout.addWidget(self.log_text)

        layout.addWidget(log_group)
        layout.addStretch()

    def _on_traction(self):
        self.manual_ctrl.set_traction()
        self.recorder.record("操作", "牵引")
        self.status_label.setText("牵引中")

    def _on_coast(self):
        self.manual_ctrl.set_coast()
        self.recorder.record("操作", "惰行")
        self.status_label.setText("惰行中")

    def _on_service_brake(self):
        self.manual_ctrl.set_service_brake()
        self.recorder.record("操作", "常用制动")
        self.status_label.setText("制动中")

    def _on_emergency_brake(self):
        self.manual_ctrl.set_emergency_brake()
        self.recorder.record("紧急制动", "按下紧急制动按钮")
        self.status_label.setText("⚠ 紧急制动！")

    def _on_open_left_door(self):
        allowed, reason = self.interlock.can_open_door()
        if not allowed:
            self.status_label.setText(f"禁止开门: {reason}")
            return
        side = self.interlock.get_allowed_door_side()
        if side == DoorSide.RIGHT:
            self.status_label.setText("此处只能开右门")
            return
        self.manual_ctrl.open_left_door()
        self.recorder.record("操作", "开左门")
        self.status_label.setText("左门已开")

    def _on_open_right_door(self):
        allowed, reason = self.interlock.can_open_door()
        if not allowed:
            self.status_label.setText(f"禁止开门: {reason}")
            return
        side = self.interlock.get_allowed_door_side()
        if side == DoorSide.LEFT:
            self.status_label.setText("此处只能开左门")
            return
        self.manual_ctrl.open_right_door()
        self.recorder.record("操作", "开右门")
        self.status_label.setText("右门已开")

    def _on_close_door(self):
        self.manual_ctrl.close_door()
        self.recorder.record("操作", "关门")
        self.status_label.setText("车门已关")

    def _on_manual_mode(self):
        self.manual_ctrl.vehicle.running_mode = RunningMode.MANUAL
        self.status_label.setText("切换为手动模式")

    def _on_auto_mode(self):
        self.manual_ctrl.vehicle.running_mode = RunningMode.AUTOMATIC
        # 设置目标为下一个车站
        pos = self.manual_ctrl.vehicle.position
        next_station = self.interlock.track.get_nearest_station_ahead(pos)
        if next_station:
            self.auto_ctrl.set_target(next_station.position)
            self.status_label.setText(f"自动驾驶中，目标: {next_station.name}")
        else:
            self.status_label.setText("无目标车站")

    def update_log(self, recorder: Recorder):
        """更新日志显示"""
        if recorder.events:
            last = recorder.events[-1]
            self.log_text.append(f"[{last.timestamp:.1f}s] {last.event_type}: {last.description}")
