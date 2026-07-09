"""控制面板 — 列车操作按钮（新版 VehicleController）

控制内容:
    - 驾驶操作：牵引 / 惰行 / 常用制动 / 紧急制动（2×2 网格）
    - 车门控制：开左门 / 开右门 / 关门
    - 列车设置：载荷等级 + 运行模式（合并为一行）
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QTextEdit, QGroupBox, QComboBox, QGridLayout,
)
from PyQt5.QtCore import Qt

from src.vehicle.vehicle_controller import VehicleController
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.enums import RunningMode, DoorSide, LoadLevel
from src.common.track_position import ITrackQuery
from src.door.interlock import DoorInterlock
from src.logger.recorder import Recorder


class ControlPanel(QWidget):
    """控制面板 — 驾驶操作按钮"""

    def __init__(self, controller: VehicleController,
                 auto_drive: AutoDriveController,
                 interlock: DoorInterlock,
                 track_adapter: ITrackQuery,
                 recorder: Recorder,
                 show_log: bool = True):
        super().__init__()
        self.controller = controller
        self.auto_drive = auto_drive
        self.interlock = interlock
        self.track_adapter = track_adapter
        self.recorder = recorder
        self.show_log = show_log
        self.log_text = None
        self._displayed_event_count = 0
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 12, 12, 12)
        self.setObjectName("controlPanel")

        # === 驾驶控制（2×2 网格，紧凑布局） ===
        drive_group = QGroupBox("驾驶控制")
        drive_group.setObjectName("panelGroup")
        drive_grid = QGridLayout(drive_group)
        drive_grid.setSpacing(5)
        drive_grid.setContentsMargins(8, 14, 8, 8)

        btn_traction = QPushButton("牵引 (FULL)")
        btn_traction.setObjectName("primaryButton")
        btn_traction.clicked.connect(self._on_traction)
        drive_grid.addWidget(btn_traction, 0, 0)

        btn_coast = QPushButton("惰行")
        btn_coast.clicked.connect(self._on_coast)
        drive_grid.addWidget(btn_coast, 0, 1)

        btn_brake = QPushButton("常用制动")
        btn_brake.setObjectName("warningButton")
        btn_brake.clicked.connect(self._on_service_brake)
        drive_grid.addWidget(btn_brake, 1, 0)

        btn_emergency = QPushButton("紧急制动")
        btn_emergency.setObjectName("dangerButton")
        btn_emergency.clicked.connect(self._on_emergency_brake)
        drive_grid.addWidget(btn_emergency, 1, 1)

        layout.addWidget(drive_group)

        # === 车门控制 ===
        door_group = QGroupBox("车门控制")
        door_group.setObjectName("panelGroup")
        door_layout = QHBoxLayout(door_group)
        door_layout.setSpacing(5)
        door_layout.setContentsMargins(8, 14, 8, 8)

        for text, slot, obj_name in [
            ("开左门", self._on_open_left_door, ""),
            ("开右门", self._on_open_right_door, ""),
            ("关门", self._on_close_door, ""),
        ]:
            btn = QPushButton(text)
            if obj_name:
                btn.setObjectName(obj_name)
            btn.clicked.connect(slot)
            door_layout.addWidget(btn)

        layout.addWidget(door_group)

        # === 列车设置（载荷 + 模式 合并为一行） ===
        setting_group = QGroupBox("列车设置")
        setting_group.setObjectName("panelGroup")
        setting_layout = QHBoxLayout(setting_group)
        setting_layout.setSpacing(8)
        setting_layout.setContentsMargins(8, 14, 8, 8)

        self.load_combo = QComboBox()
        self.load_combo.setObjectName("dataSourceCombo")
        self.load_combo.addItem("AW0 空载", LoadLevel.AW0)
        self.load_combo.addItem("AW1 满座", LoadLevel.AW1)
        self.load_combo.addItem("AW2 定员", LoadLevel.AW2)
        self.load_combo.addItem("AW3 超载", LoadLevel.AW3)
        self.load_combo.setCurrentIndex(2)
        self.load_combo.currentIndexChanged.connect(self._on_load_changed)
        setting_layout.addWidget(QLabel("载荷:"))
        setting_layout.addWidget(self.load_combo)

        btn_manual = QPushButton("手动")
        btn_manual.clicked.connect(self._on_manual_mode)
        setting_layout.addWidget(btn_manual)

        btn_auto = QPushButton("自动")
        btn_auto.clicked.connect(self._on_auto_mode)
        setting_layout.addWidget(btn_auto)

        layout.addWidget(setting_group)

        # === 操作提示 ===
        self.status_label = QLabel("就绪 — 点击「牵引」发车")
        self.status_label.setObjectName("statusHint")
        layout.addWidget(self.status_label)

        if self.show_log:
            log_group = QGroupBox("运行日志")
            log_group.setObjectName("panelGroup")
            log_layout = QVBoxLayout(log_group)
            log_layout.setContentsMargins(4, 14, 4, 4)

            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setMinimumHeight(200)
            self.log_text.setObjectName("logText")
            log_layout.addWidget(self.log_text)

            layout.addWidget(log_group, stretch=1)
        else:
            layout.addStretch()

    # ── 驾驶操作 ──────────────────────────────────────────────

    def _on_traction(self):
        self.controller.set_throttle(1.0)
        self.controller.set_brake(0.0)
        self._record_operation("牵引 (FULL)")
        self.status_label.setText("牵引中")

    def _on_coast(self):
        self.controller.coast()
        self._record_operation("惰行")
        self.status_label.setText("惰行中")

    def _on_service_brake(self):
        self.controller.set_throttle(0.0)
        self.controller.set_brake(0.4)
        self._record_operation("常用制动")
        self.status_label.setText("制动中")

    def _on_emergency_brake(self):
        self.controller.emergency_brake()
        self._record_operation("按下紧急制动按钮", event_type="紧急制动")
        self.status_label.setText("⚠ 紧急制动！")

    # ── 车门操作 ──────────────────────────────────────────────

    def _on_open_left_door(self):
        allowed, reason = self.interlock.can_open_door()
        if not allowed:
            self.status_label.setText(f"禁止开门: {reason}")
            return
        side = self.interlock.get_allowed_door_side()
        if side == DoorSide.RIGHT:
            self.status_label.setText("此处只能开右门")
            return
        self.controller.open_door(DoorSide.LEFT)
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
        self.controller.open_door(DoorSide.RIGHT)
        self._record_operation("开右门")
        self.status_label.setText("右门已开")

    def _on_close_door(self):
        self.controller.close_door()
        self._record_operation("关门")
        self.status_label.setText("车门已关")

    # ── 载荷切换 ──────────────────────────────────────────────

    def _on_load_changed(self):
        level = self.load_combo.currentData()
        if level:
            self.controller.set_load_level(level)
            self._record_operation(f"切换载荷: {level.name}")
            self.status_label.setText(f"载荷: {level.name}")

    # ── 模式切换 ──────────────────────────────────────────────

    def _on_manual_mode(self):
        self.controller.set_running_mode(RunningMode.MANUAL)
        self.controller.coast()
        self._record_operation("切换为手动模式")
        self.status_label.setText("切换为手动模式")

    def _on_auto_mode(self):
        self.controller.set_running_mode(RunningMode.AUTOMATIC)
        head_abs = 0.0
        if self.controller.states:
            head_abs = self.track_adapter.to_absolute(self.controller.states[0].position)
        next_station = self.interlock.track.get_nearest_station_ahead(head_abs)
        if next_station:
            target = self.track_adapter.from_absolute(next_station.position)
            self.auto_drive.set_target(target)
            self._record_operation(f"切换为自动模式，目标: {next_station.name}")
            self.status_label.setText(f"自动驾驶中，目标: {next_station.name}")
        else:
            self._record_operation("切换为自动模式，无目标车站")
            self.status_label.setText("无目标车站")

    # ── 日志 ──────────────────────────────────────────────────

    def update_log(self, recorder: Recorder):
        if self.log_text is None:
            return
        for event in recorder.events[self._displayed_event_count:]:
            self.log_text.append(self._format_log_event(event))
        self._displayed_event_count = len(recorder.events)
        self.log_text.moveCursor(self.log_text.textCursor().End)

    def _record_operation(self, description: str, event_type: str = "操作"):
        """记录带头车状态的操作事件"""
        head_speed = self.controller.head_speed
        head_abs = 0.0
        if self.controller.states:
            head_abs = self.track_adapter.to_absolute(self.controller.states[0].position)
        self.recorder.record(event_type, description, head_abs, head_speed)

    def _format_log_event(self, event) -> str:
        color = self._event_color(event.event_type)
        meta = f"位置 {event.position:.1f} m · 速度 {event.speed * 3.6:.1f} km/h"
        description = event.description.replace("状态快照: ", "")
        return (
            "<div style='margin:0 0 6px 0;'>"
            f"<span style='color:#93c5fd;'>[{event.timestamp:5.1f}s]</span> "
            f"<span style='color:{color}; font-weight:700;'>● {event.event_type}</span>"
            f"<div style='color:#f8fafc; margin-top:1px;'>{description}</div>"
            f"<div style='color:#94a3b8; font-size:12px; margin-top:1px;'>{meta}</div>"
            "</div>"
        )

    def _event_color(self, event_type: str) -> str:
        if event_type in ("紧急制动", "超速", "红灯违规"):
            return "#f87171"
        if event_type == "信号":
            return "#fbbf24"
        if event_type == "状态":
            return "#38bdf8"
        if event_type == "操作":
            return "#86efac"
        return "#c4b5fd"
