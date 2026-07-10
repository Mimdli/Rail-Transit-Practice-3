"""控制面板 — 列车操作按钮（新版 VehicleController）

控制内容:
    - 驾驶操作：牵引 / 惰行 / 常用制动 / 紧急制动（2×2 网格）
    - 车门控制：开左门 / 开右门 / 关门
    - 列车设置：载荷等级 + 运行模式（合并为一行）
"""

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QFrame, QTextEdit, QGroupBox, QComboBox, QGridLayout,
    QSpinBox, QButtonGroup, QScrollArea, QProgressBar,
)
from PyQt5.QtCore import Qt, pyqtSignal
from typing import TYPE_CHECKING

from src.vehicle.vehicle_controller import VehicleController
from src.vehicle.auto_drive import AutoDriveController
from src.vehicle.enums import RunningMode, DoorSide, LoadLevel, VehicleState, ControlLevel
from src.common.track_position import ITrackQuery
from src.common.consist import TrainConsist, CONSIST_4M2T, CONSIST_6M0T, CONSIST_1M4T
from src.common.car_config import MOTOR_CAR_CONFIG, TRAILER_CAR_CONFIG
from src.door.interlock import DoorInterlock
from src.logger.recorder import Recorder
from src.dispatch.models import TrainStatus

if TYPE_CHECKING:
    from src.vehicle.route_manager import RouteManager


class ControlPanel(QWidget):
    """控制面板 — 驾驶操作按钮"""

    consist_changed = pyqtSignal(object)  # 携带新的 TrainConsist
    mode_change_requested = pyqtSignal(RunningMode)  # 请求切换驾驶模式 → MainWindow.set_driving_mode()

    def __init__(self, controller: VehicleController,
                 auto_drive: AutoDriveController,
                 interlock: DoorInterlock,
                 track_adapter: ITrackQuery,
                 recorder: Recorder,
                 show_log: bool = True,
                 route_manager: "RouteManager | None" = None):
        super().__init__()
        self.controller = controller
        self.auto_drive = auto_drive
        self.interlock = interlock
        self.track_adapter = track_adapter
        self.recorder = recorder
        self.show_log = show_log
        self._route_manager = route_manager
        self.log_text = None
        self._displayed_event_count = 0
        self._car_type_buttons = []
        self._car_count = 6
        self.runtime = None
        self._init_ui()

    def bind_runtime(self, controller: VehicleController,
                     auto_drive: AutoDriveController,
                     interlock: DoorInterlock,
                     track_adapter: ITrackQuery,
                     train_id: str, runtime=None):
        """把驾驶按钮切换到指定调度列车。"""
        self.controller = controller
        self.auto_drive = auto_drive
        self.interlock = interlock
        self.track_adapter = track_adapter
        self.runtime = runtime
        self.populate_routes(auto_drive.available_routes)
        self.status_label.setText(f"当前控制：{train_id} · {controller.running_mode.value}")

    def _init_ui(self):
        # 外层布局
        outer_layout = QVBoxLayout(self)
        outer_layout.setSpacing(0)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.setObjectName("controlPanel")

        # 滚动区域 —— 包裹所有控制组
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setObjectName("controlScroll")

        scroll_content = QWidget()
        scroll_content.setObjectName("scrollContent")
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 12, 12, 12)

        # === 驾驶模式（独立大块，最顶部） ===
        mode_group = QGroupBox("驾驶模式")
        mode_group.setObjectName("modeGroup")
        mode_layout = QVBoxLayout(mode_group)
        mode_layout.setSpacing(8)
        mode_layout.setContentsMargins(12, 16, 12, 12)

        # 大号模式状态指示
        self._mode_indicator = QLabel("● 手动驾驶")
        self._mode_indicator.setObjectName("modeIndicator")
        mode_layout.addWidget(self._mode_indicator)

        # 模式描述
        self._mode_desc = QLabel("通过司控器手柄控制列车牵引与制动")
        self._mode_desc.setObjectName("modeDesc")
        mode_layout.addWidget(self._mode_desc)

        # 大号切换按钮
        self._btn_mode_toggle = QPushButton("切换至自动驾驶")
        self._btn_mode_toggle.setObjectName("modeToggleBtn")
        self._btn_mode_toggle.clicked.connect(self._on_toggle_mode)
        mode_layout.addWidget(self._btn_mode_toggle)

        layout.addWidget(mode_group)

        # === 运行控制（发车/停车/紧急） ===
        run_group = QGroupBox("运行控制")
        run_group.setObjectName("panelGroup")
        run_layout = QVBoxLayout(run_group)
        run_layout.setSpacing(6)
        run_layout.setContentsMargins(8, 14, 8, 8)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_row.setSpacing(5)
        btn_start = QPushButton("发车")
        btn_start.setObjectName("primaryButton")
        btn_start.clicked.connect(self._on_start)
        btn_row.addWidget(btn_start)

        btn_stop = QPushButton("停车")
        btn_stop.setObjectName("warningButton")
        btn_stop.clicked.connect(self._on_stop)
        btn_row.addWidget(btn_stop)

        btn_emergency = QPushButton("紧急制动")
        btn_emergency.setObjectName("dangerButton")
        btn_emergency.clicked.connect(self._on_emergency_brake)
        btn_row.addWidget(btn_emergency)
        run_layout.addLayout(btn_row)

        layout.addWidget(run_group)

        # === 牵引/制动手柄（司控器） ===
        self._handle_group = QGroupBox("牵引/制动手柄（司控器）")
        handle_group = self._handle_group
        handle_group.setObjectName("panelGroup")
        handle_layout = QVBoxLayout(handle_group)
        handle_layout.setSpacing(6)
        handle_layout.setContentsMargins(8, 14, 8, 10)

        # ── 力度百分比指示条 ──────────────────────────────────
        pct_frame = QFrame()
        pct_frame.setObjectName("handleBarFrame")
        pct_layout = QHBoxLayout(pct_frame)
        pct_layout.setSpacing(0)
        pct_layout.setContentsMargins(0, 0, 0, 0)

        # 制动百分比条
        self._brake_bar = QProgressBar()
        self._brake_bar.setObjectName("brakeBar")
        self._brake_bar.setRange(0, 100)
        self._brake_bar.setValue(0)
        self._brake_bar.setTextVisible(False)
        self._brake_bar.setFixedHeight(22)
        self._brake_bar.setInvertedAppearance(True)  # 从右向左填充

        # 百分比数字
        self._handle_pct_label = QLabel("0%")
        self._handle_pct_label.setObjectName("handlePctLabel")
        self._handle_pct_label.setAlignment(Qt.AlignCenter)
        self._handle_pct_label.setFixedWidth(64)

        # 牵引百分比条
        self._traction_bar = QProgressBar()
        self._traction_bar.setObjectName("tractionBar")
        self._traction_bar.setRange(0, 100)
        self._traction_bar.setValue(0)
        self._traction_bar.setTextVisible(False)
        self._traction_bar.setFixedHeight(22)

        pct_layout.addWidget(self._brake_bar)
        pct_layout.addWidget(self._handle_pct_label)
        pct_layout.addWidget(self._traction_bar)
        handle_layout.addWidget(pct_frame)

        # ── 手柄按钮组（互斥） ─────────────────────────────────
        self._handle_button_group = QButtonGroup(self)
        self._handle_button_group.setExclusive(True)
        self._handle_buttons: dict = {}  # ControlLevel → QPushButton

        # P3/P2/P1 牵引行（绿色系按钮）
        trac_row = QHBoxLayout()
        trac_row.setSpacing(4)
        for level, label, pct in [
            (ControlLevel.FULL_TRACTION, "P3 全牵引", "100%"),
            (ControlLevel.MEDIUM_TRACTION, "P2 中牵引", "66%"),
            (ControlLevel.LOW_TRACTION, "P1 低牵引", "33%"),
        ]:
            btn = QPushButton(f"{label}\n{pct}")
            btn.setObjectName("tractionHandleBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, l=level: self._on_handle_clicked(l))
            self._handle_button_group.addButton(btn)
            self._handle_buttons[level] = btn
            trac_row.addWidget(btn)
        handle_layout.addLayout(trac_row)

        # COAST 行
        coast_row = QHBoxLayout()
        coast_row.setSpacing(4)
        btn_coast = QPushButton("COAST\n惰行")
        btn_coast.setObjectName("coastHandleBtn")
        btn_coast.setCheckable(True)
        btn_coast.clicked.connect(lambda checked: self._on_handle_clicked(ControlLevel.COAST))
        self._handle_button_group.addButton(btn_coast)
        self._handle_buttons[ControlLevel.COAST] = btn_coast
        coast_row.addWidget(btn_coast)
        coast_row.addStretch()
        handle_layout.addLayout(coast_row)

        # B1/B2/EB 制动行（红色系按钮）
        brake_row = QHBoxLayout()
        brake_row.setSpacing(4)
        for level, label, pct in [
            (ControlLevel.SERVICE_BRAKE, "B1 常用制动", "40%"),
            (ControlLevel.FULL_BRAKE, "B2 全制动", "70%"),
            (ControlLevel.EMERGENCY_BRAKE, "EB 紧急制动", "100%"),
        ]:
            btn = QPushButton(f"{label}\n{pct}")
            btn.setObjectName("brakeHandleBtn")
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, l=level: self._on_handle_clicked(l))
            self._handle_button_group.addButton(btn)
            self._handle_buttons[level] = btn
            brake_row.addWidget(btn)
        handle_layout.addLayout(brake_row)

        # 当前级位文本
        self._handle_level_label = QLabel("当前级位: COAST 惰行")
        self._handle_level_label.setObjectName("handleLevelLabel")
        self._handle_level_label.setAlignment(Qt.AlignCenter)
        handle_layout.addWidget(self._handle_level_label)

        layout.addWidget(handle_group)

        # === 车门控制 ===
        door_group = QGroupBox("车门控制")
        door_group.setObjectName("panelGroup")
        door_layout = QHBoxLayout(door_group)
        door_layout.setSpacing(5)
        door_layout.setContentsMargins(8, 12, 8, 7)

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

        # === 列车设置（载荷 + 停站时间 + 模式 合并为一行） ===
        setting_group = QGroupBox("列车设置")
        setting_group.setObjectName("panelGroup")
        setting_layout = QHBoxLayout(setting_group)
        setting_layout.setSpacing(6)
        setting_layout.setContentsMargins(8, 12, 8, 7)

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

        # 停站时间
        setting_layout.addWidget(QLabel("停站:"))
        self.dwell_spin = QSpinBox()
        self.dwell_spin.setRange(5, 120)
        self.dwell_spin.setValue(20)
        self.dwell_spin.setSuffix(" s")
        self.dwell_spin.setToolTip("自动模式下停站时间（含开关门）")
        self.dwell_spin.valueChanged.connect(self._on_dwell_changed)
        setting_layout.addWidget(self.dwell_spin)

        layout.addWidget(setting_group)

        # === 进路选择 ===
        route_group = QGroupBox("进路选择")
        route_group.setObjectName("panelGroup")
        route_layout = QHBoxLayout(route_group)
        route_layout.setSpacing(6)
        route_layout.setContentsMargins(8, 12, 8, 7)

        self.route_combo = QComboBox()
        self.route_combo.setObjectName("dataSourceCombo")
        self.route_combo.setMinimumWidth(140)
        self.route_combo.currentIndexChanged.connect(self._on_route_changed)
        route_layout.addWidget(QLabel("路线:"))
        route_layout.addWidget(self.route_combo)

        layout.addWidget(route_group)

        # === 重置位置 ===
        reset_group = QGroupBox("重置位置")
        reset_group.setObjectName("panelGroup")
        reset_layout = QHBoxLayout(reset_group)
        reset_layout.setSpacing(8)
        reset_layout.setContentsMargins(8, 14, 8, 8)

        self.station_combo = QComboBox()
        self.station_combo.setObjectName("dataSourceCombo")
        self.station_combo.setMinimumWidth(120)
        self.station_combo.setToolTip("选择要重置到的车站")
        reset_layout.addWidget(QLabel("车站:"))
        reset_layout.addWidget(self.station_combo, stretch=1)

        btn_reset = QPushButton("跳转")
        btn_reset.clicked.connect(self._on_reset_to_station)
        reset_layout.addWidget(btn_reset)

        layout.addWidget(reset_group)

        # === 编组配置 ===
        consist_group = QGroupBox("编组配置")
        consist_group.setObjectName("panelGroup")
        consist_layout = QVBoxLayout(consist_group)
        consist_layout.setSpacing(5)
        consist_layout.setContentsMargins(8, 12, 8, 7)

        # 预设按钮行
        preset_hlayout = QHBoxLayout()
        preset_hlayout.setSpacing(4)
        btn_4m2t = QPushButton("4M2T")
        btn_4m2t.setObjectName("smallButton")
        btn_4m2t.clicked.connect(lambda: self._apply_preset(6, [True, False, True, True, False, True]))
        btn_6m0t = QPushButton("6M0T")
        btn_6m0t.setObjectName("smallButton")
        btn_6m0t.clicked.connect(lambda: self._apply_preset(6, [True]*6))
        btn_1m4t = QPushButton("1M4T")
        btn_1m4t.setObjectName("smallButton")
        btn_1m4t.clicked.connect(lambda: self._apply_preset(5, [True]+[False]*4))
        btn_4m4t = QPushButton("4M4T")
        btn_4m4t.setObjectName("smallButton")
        btn_4m4t.clicked.connect(lambda: self._apply_preset(8, [True, False, True, False, False, True, False, True]))
        for b in (btn_4m2t, btn_6m0t, btn_1m4t, btn_4m4t):
            preset_hlayout.addWidget(b)
        consist_layout.addLayout(preset_hlayout)

        # 车厢数 + 按钮行（组合）
        count_hlayout = QHBoxLayout()
        count_hlayout.setSpacing(4)
        count_hlayout.addWidget(QLabel("车厢数:"))
        self._car_count_spin = QSpinBox()
        self._car_count_spin.setRange(2, 8)
        self._car_count_spin.setValue(6)
        self._car_count_spin.valueChanged.connect(self._on_car_count_changed)
        count_hlayout.addWidget(self._car_count_spin)
        count_hlayout.addStretch()
        consist_layout.addLayout(count_hlayout)

        # 每节车厢的 M/T 按钮行
        self._car_type_layout = QHBoxLayout()
        self._car_type_layout.setSpacing(2)
        self._rebuild_car_type_buttons(6)
        consist_layout.addLayout(self._car_type_layout)

        # 应用按钮
        btn_apply = QPushButton("应用编组")
        btn_apply.setObjectName("primaryButton")
        btn_apply.clicked.connect(self._on_apply_consist)
        consist_layout.addWidget(btn_apply)

        layout.addWidget(consist_group)

        # === 操作提示 ===
        self.status_label = QLabel("就绪 — 点击「发车」启动")
        self.status_label.setObjectName("statusHint")
        layout.addWidget(self.status_label)

        if self.show_log:
            log_group = QGroupBox("运行日志")
            log_group.setObjectName("panelGroup")
            log_layout = QVBoxLayout(log_group)
            log_layout.setContentsMargins(4, 14, 4, 4)

            self.log_text = QTextEdit()
            self.log_text.setReadOnly(True)
            self.log_text.setMinimumHeight(150)
            self.log_text.setMaximumHeight(250)
            self.log_text.setObjectName("logText")
            log_layout.addWidget(self.log_text)

            layout.addWidget(log_group)
        else:
            layout.addStretch()

        # ── 将内容装入滚动区域 ──────────────────────────────────
        scroll.setWidget(scroll_content)
        outer_layout.addWidget(scroll)

    # ── 级位百分比映射 ──────────────────────────────────────────
    _HANDLE_PCT = {
        ControlLevel.FULL_TRACTION:    (100, 0),
        ControlLevel.MEDIUM_TRACTION:  (66, 0),
        ControlLevel.LOW_TRACTION:     (33, 0),
        ControlLevel.COAST:            (0, 0),
        ControlLevel.SERVICE_BRAKE:    (0, 40),
        ControlLevel.FULL_BRAKE:       (0, 70),
        ControlLevel.EMERGENCY_BRAKE:  (0, 100),
    }

    # ── 驾驶操作 ──────────────────────────────────────────────

    def _on_handle_clicked(self, level: ControlLevel):
        """司控器手柄点击 — 应用离散控制级位。"""
        self.controller.apply_control_level(level)
        name = self._control_level_name(level)
        self._record_operation(f"手柄 → {name}")
        self._reset_status_style()
        self.status_label.setText(f"手柄: {name}")
        self._handle_level_label.setText(f"当前级位: {name}")
        self._update_handle_pct_display(level)

    def _sync_handle_button(self, level: ControlLevel):
        """同步手柄按钮组选中状态到指定级位。"""
        btn = self._handle_buttons.get(level)
        if btn is not None:
            btn.setChecked(True)
        self._handle_level_label.setText(f"当前级位: {self._control_level_name(level)}")
        self._update_handle_pct_display(level)

    def _update_handle_pct_display(self, level: ControlLevel):
        """根据当前级位更新百分比进度条和标签。"""
        trac_pct, brake_pct = self._HANDLE_PCT.get(level, (0, 0))
        self._traction_bar.setValue(trac_pct)
        self._brake_bar.setValue(brake_pct)

        if trac_pct > 0:
            self._handle_pct_label.setText(f"牵引\n{trac_pct}%")
            self._handle_pct_label.setStyleSheet(
                "font-size: 16px; font-weight: 800; color: #16a34a; padding: 2px 4px;")
        elif brake_pct > 0:
            self._handle_pct_label.setText(f"制动\n{brake_pct}%")
            self._handle_pct_label.setStyleSheet(
                "font-size: 16px; font-weight: 800; color: #dc2626; padding: 2px 4px;")
        else:
            self._handle_pct_label.setText("惰行\n0%")
            self._handle_pct_label.setStyleSheet(
                "font-size: 16px; font-weight: 800; color: #64748b; padding: 2px 4px;")

    def _control_level_name(self, level: ControlLevel) -> str:
        """ControlLevel → 中文名称。"""
        names = {
            ControlLevel.FULL_TRACTION: "P3 全牵引",
            ControlLevel.MEDIUM_TRACTION: "P2 中牵引",
            ControlLevel.LOW_TRACTION: "P1 低牵引",
            ControlLevel.COAST: "COAST 惰行",
            ControlLevel.SERVICE_BRAKE: "B1 常用制动",
            ControlLevel.FULL_BRAKE: "B2 全制动",
            ControlLevel.EMERGENCY_BRAKE: "EB 紧急制动",
        }
        return names.get(level, "未知")

    def _on_emergency_brake(self):
        self._activate_direct_control()
        self.controller.emergency_brake()
        self._record_operation("按下紧急制动按钮", event_type="紧急制动")
        self._reset_status_style()
        self.status_label.setText("⚠ 紧急制动！")
        # 同步手柄显示
        self._sync_handle_button(ControlLevel.EMERGENCY_BRAKE)

    def _on_start(self):
        """发车按钮：关门+联锁检查+释放制动+全牵引。"""
        # 先关门
        self.controller.close_door()
        success = self.controller.start_moving(throttle_level=1.0)
        if success:
            self._record_operation("发车")
            self._reset_status_style()
            self.status_label.setText("发车中 → 全牵引")
            # 发车后手柄显示同步到 COAST（start_moving 绕过了手柄映射）
            self._sync_handle_button(ControlLevel.COAST)
        else:
            reason = ""
            if self.controller.any_door_open:
                reason = "（门未关）"
            elif not self.controller.interlock.traction_permitted:
                reason = "（牵引未授权）"
            elif self.controller.interlock.emergency_brake_required:
                reason = "（紧急制动中）"
            else:
                reason = f"（状态: {self.controller.vehicle_state.name}）"
            self._show_mode_transition(False, f"✗ 无法发车 {reason}")

    def _on_stop(self):
        """停车按钮：施加常用制动。"""
        self.controller.command_stop(brake_level=0.4)
        self._record_operation("停车")
        self._reset_status_style()
        self.status_label.setText("停车中（常用制动）")
        # 同步手柄按钮到常用制动位
        self._sync_handle_button(ControlLevel.SERVICE_BRAKE)

    def _on_reset_to_station(self):
        """跳转到选定车站。"""
        idx = self.station_combo.currentIndex()
        if idx < 0:
            return
        # 获取车站绝对位置
        stations = self.interlock.track.stations
        if idx >= len(stations):
            return
        station = stations[idx]
        abs_pos = station.position if hasattr(station, 'position') else station.abs_pos
        success = self.controller.reset_to_absolute(abs_pos)
        if success:
            self._record_operation(f"跳转到 {station.name} ({abs_pos:.0f}m)")
            self.status_label.setText(f"已跳转到 {station.name}")
            # 通知主窗口更新可视化
            if hasattr(self, 'consist_changed'):
                self.consist_changed.emit(self.controller.consist)
        else:
            self.status_label.setText("跳转失败")

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

    def _on_toggle_mode(self):
        """切换驾驶模式（手动 ↔ 自动）—— 通过信号委托 MainWindow 统一处理。"""
        if self.controller.running_mode == RunningMode.AUTOMATIC:
            self.mode_change_requested.emit(RunningMode.MANUAL)
        else:
            self.mode_change_requested.emit(RunningMode.AUTOMATIC)

    def _update_mode_display(self):
        """更新模式指示器、描述、切换按钮文本，以及手动控制组的显隐。"""
        if self.controller.running_mode == RunningMode.AUTOMATIC:
            self._mode_indicator.setText("○ 自动驾驶")
            self._mode_indicator.setStyleSheet(
                "font-size: 20px; font-weight: 800; color: #16a34a; padding: 4px 0;")
            self._mode_desc.setText("系统自动控制列车运行、停站与开关门")
            self._btn_mode_toggle.setText("切换至手动驾驶")
            # 隐藏手动司控器手柄
            self._handle_group.setVisible(False)
        else:
            self._mode_indicator.setText("● 手动驾驶")
            self._mode_indicator.setStyleSheet(
                "font-size: 20px; font-weight: 800; color: #2563eb; padding: 4px 0;")
            self._mode_desc.setText("通过司控器手柄控制列车牵引与制动")
            self._btn_mode_toggle.setText("切换至自动驾驶")
            # 显示手动司控器手柄
            self._handle_group.setVisible(True)

    def _show_mode_transition(self, success: bool, message: str):
        """用醒目的样式显示模式切换结果。

        Args:
            success: True = 成功（绿），False = 失败（红）。
            message: 要显示的提示文字。
        """
        if success:
            self.status_label.setStyleSheet(
                "background: #dcfce7; border: 2px solid #16a34a; border-radius: 7px;"
                "color: #166534; padding: 12px 14px; font-size: 16px; font-weight: 700;")
        else:
            self.status_label.setStyleSheet(
                "background: #fef2f2; border: 2px solid #dc2626; border-radius: 7px;"
                "color: #991b1b; padding: 12px 14px; font-size: 16px; font-weight: 700;")
        self.status_label.setText(message)

    def _reset_status_style(self):
        """将状态标签恢复为默认样式。"""
        self.status_label.setStyleSheet(
            "background: #fff7ed; border: 1px solid #fed7aa; border-radius: 7px;"
            "color: #9a3412; padding: 10px 12px; font-size: 16px; font-weight: 700;")

    def _on_manual_mode(self):
        """切换到手动模式：关门、重置自动驾驶状态、保持制动。"""
        # 先关门（避免门开着就切手动）
        self.controller.close_door()
        # 重置自动驾驶状态机
        self.auto_drive.reset_state()
        # 切换到手动模式
        self.controller.set_running_mode(RunningMode.MANUAL)
        # 施加常用制动（通过高层指令）
        self.controller.command_stop(brake_level=0.4)
        self._update_mode_display()
        # 同步手柄按钮到常用制动位
        self._sync_handle_button(ControlLevel.SERVICE_BRAKE)
        self._record_operation("切换手动模式")
        self._show_mode_transition(True, "✓ 已切换至手动驾驶 — 请操作司控器手柄")

    def _on_auto_mode(self):
        """切换到自动模式：关门、找下一站、设目标、切模式。"""
        # 先关门（安全：不允许门开着发车）
        self.controller.close_door()

        head_abs = 0.0
        if self.controller.states:
            head_abs = self.track_adapter.to_absolute(self.controller.states[0].position)

        # 查找下一站（通过 AutoDriveController 统一入口）
        next_station = self.auto_drive.find_next_station(head_abs)

        if next_station is None:
            self._show_mode_transition(False, "✗ 切换失败: 无前方车站")
            self._record_operation("切换自动失败: 无前方车站")
            return

        # 先设目标（确保 auto_drive 状态就绪），后切模式
        from src.common.track_position import TrackPosition
        target = self.track_adapter.from_absolute(next_station.position)
        self.auto_drive.set_target(target)

        self.controller.set_running_mode(RunningMode.AUTOMATIC)
        self._update_mode_display()
        self._record_operation(f"切换自动模式，目标: {next_station.name}")
        self._show_mode_transition(
            True, f"✓ 已切换至自动驾驶 → 目标: {next_station.name}")

    def _on_dwell_changed(self, value: int):
        """停站时间变更。"""
        self.auto_drive.dwell_time = float(value)
        self.status_label.setText(f"停站时间: {value}s")

    def _activate_direct_control(self):
        """运行仿真页操作优先，切换为直接驾驶状态。"""
        if self.runtime is None:
            return
        self.runtime.held = False
        self.runtime.emergency = False
        self.runtime.blocked_reason = ""
        self.runtime.status = TrainStatus.MANUAL
        self.controller.interlock.emergency_brake_required = False
        self.controller.interlock.traction_permitted = True

    # ── 进路切换 ──────────────────────────────────────────────

    def _on_route_changed(self):
        """用户通过下拉框手动切换进路。"""
        idx = self.route_combo.currentIndex()
        # 优先从 RouteManager 获取进路列表，否则从 AutoDriveController
        if self._route_manager is not None:
            routes = self._route_manager.available_routes
        else:
            routes = self.auto_drive.available_routes
        if 0 <= idx < len(routes):
            route = routes[idx]
            # 优先通过 RouteManager 设置进路
            if self._route_manager is not None:
                self._route_manager.set_route(route)
            else:
                self.auto_drive.set_route(route)
            if route.is_auto:
                self.status_label.setText("进路: 自动（系统算路）")
                # 如果正在自动驾驶中，用当前 target 重新算路
                if (self.controller.running_mode == RunningMode.AUTOMATIC
                        and self.auto_drive.target_position is not None):
                    self.auto_drive.set_target(self.auto_drive.target_position)
            else:
                self.status_label.setText(f"进路: {route.name}")

    def populate_stations(self, stations):
        """填充车站下拉框。

        Args:
            stations: Station 列表。
        """
        self.station_combo.blockSignals(True)
        self.station_combo.clear()
        for st in stations:
            abs_pos = st.position if hasattr(st, 'position') else st.abs_pos
            self.station_combo.addItem(f"{st.name} ({abs_pos:.0f}m)", abs_pos)
        self.station_combo.blockSignals(False)

    def populate_routes(self, routes):
        """填充进路下拉框。

        Args:
            routes: Route 列表，第一个应是"自动"模式。
        """
        self.route_combo.blockSignals(True)
        self.route_combo.clear()
        for r in routes:
            label = r.name if not r.is_auto else "自动（系统算路）"
            self.route_combo.addItem(label, r.route_id)
        self.route_combo.blockSignals(False)

    # ── 编组配置 ───────────────────────────────────────────────

    def _rebuild_car_type_buttons(self, count: int):
        """重建 M/T 切换按钮行。"""
        self._car_count = count
        # 清除旧按钮
        while self._car_type_layout.count():
            item = self._car_type_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._car_type_buttons.clear()
        # 新建按钮（默认 4M2T 模式）
        for i in range(count):
            is_motor = i < count - 2  # 默认后两节为拖车
            btn = QPushButton("M" if is_motor else "T")
            btn.setObjectName("smallButton")
            btn.setCheckable(True)
            btn.setChecked(is_motor)
            btn.clicked.connect(lambda checked, idx=i: self._on_toggle_car_type(idx))
            self._car_type_layout.addWidget(btn)
            self._car_type_buttons.append(btn)

    def _on_car_count_changed(self, value: int):
        """车厢数变化时重建按钮行，保留已存在的 M/T 配置。"""
        old_types = [b.isChecked() for b in self._car_type_buttons]
        self._rebuild_car_type_buttons(value)
        # 尽量保留旧配置的前缀
        for i in range(min(len(old_types), value)):
            self._car_type_buttons[i].setChecked(old_types[i])

    def _on_toggle_car_type(self, idx: int):
        """切换第 idx 节车厢的 M/T。"""
        btn = self._car_type_buttons[idx]
        btn.setText("M" if btn.isChecked() else "T")

    def _apply_preset(self, count: int, motor_states: list):
        """应用预设编组。"""
        self._car_count_spin.blockSignals(True)
        self._car_count_spin.setValue(count)
        self._car_count_spin.blockSignals(False)
        self._car_count = count
        self._rebuild_car_type_buttons(count)
        for i, motor in enumerate(motor_states):
            if i < count:
                self._car_type_buttons[i].setChecked(motor)
                self._car_type_buttons[i].setText("M" if motor else "T")

    def _on_apply_consist(self):
        """根据当前 M/T 配置构建 TrainConsist 并发射信号。"""
        configs = []
        for btn in self._car_type_buttons:
            if btn.isChecked():
                configs.append(MOTOR_CAR_CONFIG)
            else:
                configs.append(TRAILER_CAR_CONFIG)
        new_consist = TrainConsist(configs)
        # 在控制器上立即生效
        self.controller.replace_consist(new_consist, self.track_adapter)
        # 通知主窗口更新可视化
        self.consist_changed.emit(new_consist)
        motor = sum(1 for b in self._car_type_buttons if b.isChecked())
        trailer = len(self._car_type_buttons) - motor
        self.status_label.setText(f"编组已更新: {motor}M{trailer}T")

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
