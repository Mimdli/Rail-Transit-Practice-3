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
                 interlock: DoorInterlock, recorder: Recorder, show_log: bool = True):
        super().__init__()
        self.manual_ctrl = manual_ctrl
        self.auto_ctrl = auto_ctrl
        self.interlock = interlock
        self.recorder = recorder
        self.show_log = show_log
        self.log_text = None
        self._displayed_event_count = 0
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 16, 16, 16)
        self.setObjectName("controlPanel")

        # === 驾驶控制 ===
        drive_group = QGroupBox("驾驶控制")
        drive_group.setObjectName("panelGroup")
        drive_layout = QVBoxLayout(drive_group)
        drive_layout.setSpacing(8)

        # 牵引
        btn_traction = QPushButton("牵引")
        btn_traction.setObjectName("primaryButton")
        btn_traction.clicked.connect(self._on_traction)
        drive_layout.addWidget(btn_traction)

        # 惰行
        btn_coast = QPushButton("惰行")
        btn_coast.setObjectName("secondaryButton")
        btn_coast.clicked.connect(self._on_coast)
        drive_layout.addWidget(btn_coast)

        # 常用制动
        btn_brake = QPushButton("常用制动")
        btn_brake.setObjectName("warningButton")
        btn_brake.clicked.connect(self._on_service_brake)
        drive_layout.addWidget(btn_brake)

        # 紧急制动
        btn_emergency = QPushButton("紧急制动")
        btn_emergency.setObjectName("dangerButton")
        btn_emergency.clicked.connect(self._on_emergency_brake)
        drive_layout.addWidget(btn_emergency)

        layout.addWidget(drive_group)

        # === 车门控制 ===
        door_group = QGroupBox("车门控制")
        door_group.setObjectName("panelGroup")
        door_layout = QHBoxLayout(door_group)
        door_layout.setSpacing(8)

        btn_left_door = QPushButton("开左门")
        btn_left_door.setObjectName("secondaryButton")
        btn_left_door.clicked.connect(self._on_open_left_door)
        door_layout.addWidget(btn_left_door)

        btn_right_door = QPushButton("开右门")
        btn_right_door.setObjectName("secondaryButton")
        btn_right_door.clicked.connect(self._on_open_right_door)
        door_layout.addWidget(btn_right_door)

        btn_close_door = QPushButton("关门")
        btn_close_door.setObjectName("secondaryButton")
        btn_close_door.clicked.connect(self._on_close_door)
        door_layout.addWidget(btn_close_door)

        layout.addWidget(door_group)

        # === 模式切换 ===
        mode_group = QGroupBox("运行模式")
        mode_group.setObjectName("panelGroup")
        mode_layout = QHBoxLayout(mode_group)
        mode_layout.setSpacing(8)

        btn_manual = QPushButton("手动")
        btn_manual.setObjectName("secondaryButton")
        btn_manual.clicked.connect(self._on_manual_mode)
        mode_layout.addWidget(btn_manual)

        btn_auto = QPushButton("自动")
        btn_auto.setObjectName("secondaryButton")
        btn_auto.clicked.connect(self._on_auto_mode)
        mode_layout.addWidget(btn_auto)

        layout.addWidget(mode_group)

        # === 操作提示 ===
        self.status_label = QLabel("就绪")
        self.status_label.setObjectName("statusHint")
        layout.addWidget(self.status_label)

        if self.show_log:
            # === 日志 ===
            log_group = QGroupBox("运行日志")
            log_group.setObjectName("panelGroup")
            log_layout = QVBoxLayout(log_group)

            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setMinimumHeight(300)
            self.log_text.setObjectName("logText")
            log_layout.addWidget(self.log_text)

            layout.addWidget(log_group, stretch=1)
        else:
            layout.addStretch()

    def _on_traction(self):
        self.manual_ctrl.set_traction()
        self._record_operation("牵引")
        self.status_label.setText("牵引中")

    def _on_coast(self):
        self.manual_ctrl.set_coast()
        self._record_operation("惰行")
        self.status_label.setText("惰行中")

    def _on_service_brake(self):
        self.manual_ctrl.set_service_brake()
        self._record_operation("常用制动")
        self.status_label.setText("制动中")

    def _on_emergency_brake(self):
        self.manual_ctrl.set_emergency_brake()
        self._record_operation("按下紧急制动按钮", event_type="紧急制动")
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
        self._record_operation("开左门")
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
        self._record_operation("开右门")
        self.status_label.setText("右门已开")

    def _on_close_door(self):
        self.manual_ctrl.close_door()
        self._record_operation("关门")
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
        """更新日志显示，只追加本次刷新新增的事件"""
        if self.log_text is None:
            return
        for event in recorder.events[self._displayed_event_count:]:
            self.log_text.append(self._format_log_event(event))
        self._displayed_event_count = len(recorder.events)
        self.log_text.moveCursor(self.log_text.textCursor().End)

    def _record_operation(self, description: str, event_type: str = "操作"):
        """记录带车辆状态的操作事件"""
        vehicle = self.manual_ctrl.vehicle
        self.recorder.record(event_type, description, vehicle.position, vehicle.speed)

    def _format_log_event(self, event) -> str:
        """把事件格式化成更易扫读的日志块"""
        color = self._event_color(event.event_type)
        meta = f"位置 {event.position:.1f} m · 速度 {event.speed * 3.6:.1f} km/h"
        description = event.description.replace("状态快照: ", "")
        return (
            "<div style='margin:0 0 8px 0;'>"
            f"<span style='color:#93c5fd;'>[{event.timestamp:5.1f}s]</span> "
            f"<span style='color:{color}; font-weight:700;'>● {event.event_type}</span>"
            f"<div style='color:#f8fafc; margin-top:2px;'>{description}</div>"
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
