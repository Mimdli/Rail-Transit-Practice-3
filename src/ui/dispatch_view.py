"""单机调度中心：列车命令、运行总览和区段占用显示。"""

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QBrush, QColor, QPainter, QPen
from PyQt5.QtWidgets import (
    QComboBox, QFormLayout, QGraphicsEllipseItem, QGraphicsScene,
    QGraphicsView, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from src.dispatch.dispatch_manager import DispatchManager, DispatchResult


class DispatchLineView(QGraphicsView):
    """显示车站、区段占用、进路锁闭和多列车位置。"""

    def __init__(self, manager: DispatchManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setMinimumHeight(220)
        self.setMaximumHeight(260)
        self.setObjectName("dispatchLineView")

    def set_manager(self, manager: DispatchManager):
        self.manager = manager
        self.refresh()

    def refresh(self):
        self._scene.clear()
        track = self.manager.track
        main_segments = [
            segment for segment in track.segments
            if segment.start_neighbor or segment.end_neighbor
        ] or list(track.segments)
        total = max(
            (segment.abs_start + segment.length for segment in main_segments),
            default=track.total_length() or 1.0,
        )
        width = max(self.viewport().width() - 30, 900)
        left, right = 90.0, width - 70.0
        down_y, up_y = 120.0, 180.0

        self._add_text("全线运行态势", left, 16, 15, QColor("#172033"), True)
        self._add_text(
            "双线实时显示 · 灰色空闲 · 橙色锁闭 · 红色占用",
            left, 42, 9, QColor("#475467"), False,
        )
        self._add_text("下行 →", 22, down_y - 8, 10, QColor("#2563eb"), True)
        self._add_text("上行 ←", 22, up_y - 8, 10, QColor("#0f766e"), True)

        for segment in main_segments:
            x1 = left + segment.abs_start / total * (right - left)
            x2 = left + (segment.abs_start + segment.length) / total * (right - left)
            owners = self.manager.occupancy.owners(segment.seg_id)
            lock_owner = self.manager.interlocking.locked_by(segment.seg_id)
            color = QColor("#dc2626") if owners else (
                QColor("#f59e0b") if lock_owner else QColor("#94a3b8")
            )
            for y in (down_y, up_y):
                self._scene.addLine(
                    x1, y, x2, y,
                    QPen(color, 7, Qt.SolidLine, Qt.RoundCap))
            if owners or lock_owner:
                state = (f"LK{segment.seg_id} · {','.join(sorted(owners))} 占用"
                         if owners else f"LK{segment.seg_id} · {lock_owner} 锁闭")
                self._add_text(state, (x1 + x2) / 2 - 38, up_y + 18,
                               8, color, True)

        for station in sorted(track.stations, key=lambda item: item.position):
            x = left + station.position / total * (right - left)
            self._scene.addLine(x, down_y - 20, x, up_y + 20,
                                QPen(QColor("#334155"), 2))
            self._add_text(station.name, x - 24, 72, 9,
                           QColor("#1d2939"), True)

        palette = ["#2563eb", "#7c3aed", "#0891b2", "#0f766e", "#9333ea"]
        for index, runtime in enumerate(self.manager.trains.values()):
            x = left + max(0.0, min(total, runtime.head_abs)) / total * (right - left)
            color = QColor(palette[index % len(palette)])
            direction = runtime.controller.direction
            lane_y = down_y if direction > 0 else up_y
            marker_y = lane_y - 28 if direction > 0 else lane_y + 12
            marker = QGraphicsEllipseItem(x - 8, marker_y, 16, 16)
            marker.setBrush(QBrush(color))
            marker.setPen(QPen(QColor("#ffffff"), 2))
            self._scene.addItem(marker)
            label_y = marker_y - 4 if direction > 0 else marker_y + 18
            self._add_text(
                f"{runtime.train_id} {runtime.speed_kmh:.0f}",
                x + 12, label_y, 9, color, True)

        self._scene.setSceneRect(0, 0, width, 230)

    def resizeEvent(self, event):  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.refresh()

    def _add_text(self, text, x, y, size, color, bold):
        item = self._scene.addText(text)
        font = item.font()
        font.setFamily("Microsoft YaHei UI")
        font.setPointSize(size)
        font.setBold(bold)
        item.setFont(font)
        item.setDefaultTextColor(color)
        item.setPos(x, y)


class DispatchView(QWidget):
    """调度操作页签。"""

    operation_finished = pyqtSignal(str, bool)

    COLUMNS = ("列车", "状态", "方向", "位置", "速度", "目标站", "交路", "说明")

    def __init__(self, manager: DispatchManager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setObjectName("dispatchView")
        self._build_ui()
        self.refresh_options()
        self.refresh()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("调度中心")
        title.setObjectName("pageTitle")
        self.summary = QLabel()
        self.summary.setObjectName("dataSourceStatus")
        header.addWidget(title)
        header.addWidget(self.summary, stretch=1)
        root.addLayout(header)

        self.line_view = DispatchLineView(self.manager)
        root.addWidget(self.line_view)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_command_panel())
        splitter.addWidget(self._build_train_table())
        splitter.setSizes([320, 1040])
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        root.addWidget(splitter, stretch=1)

        self.feedback = QLabel("就绪 — 添加列车并设置交路后即可发车")
        self.feedback.setObjectName("dispatchFeedback")
        root.addWidget(self.feedback)

    def _build_command_panel(self):
        panel = QWidget()
        panel.setMaximumWidth(360)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(6)

        add_group = QGroupBox("列车编组")
        form = QFormLayout(add_group)
        self.train_id_edit = QLineEdit()
        self.train_id_edit.setPlaceholderText("例如 2车")
        self.start_station_combo = QComboBox()
        self.add_button = QPushButton("加车")
        self.add_button.setObjectName("primaryButton")
        self.remove_button = QPushButton("删车")
        form.addRow("列车编号", self.train_id_edit)
        form.addRow("加入位置", self.start_station_combo)
        row = QHBoxLayout()
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        form.addRow(row)
        layout.addWidget(add_group)

        plan_group = QGroupBox("交路任务")
        plan_layout = QFormLayout(plan_group)
        self.plan_combo = QComboBox()
        self.assign_button = QPushButton("设置交路")
        plan_layout.addRow("运营交路", self.plan_combo)
        plan_layout.addRow(self.assign_button)
        layout.addWidget(plan_group)

        command_group = QGroupBox("调度命令")
        command_layout = QVBoxLayout(command_group)
        command_row = QHBoxLayout()
        self.depart_button = QPushButton("发车")
        self.depart_button.setObjectName("primaryButton")
        self.hold_button = QPushButton("扣车")
        self.hold_button.setObjectName("warningButton")
        command_row.addWidget(self.depart_button)
        command_row.addWidget(self.hold_button)
        command_layout.addLayout(command_row)
        command_row2 = QHBoxLayout()
        self.release_button = QPushButton("解除扣车")
        self.stop_button = QPushButton("紧急停车")
        self.stop_button.setObjectName("dangerButton")
        command_row2.addWidget(self.release_button)
        command_row2.addWidget(self.stop_button)
        command_layout.addLayout(command_row2)
        self.restore_button = QPushButton("解除紧急状态")
        command_layout.addWidget(self.restore_button)
        layout.addWidget(command_group)
        layout.addStretch()

        self.add_button.clicked.connect(self._add_train)
        self.remove_button.clicked.connect(self._remove_train)
        self.assign_button.clicked.connect(self._assign_plan)
        self.depart_button.clicked.connect(lambda: self._run_command("depart"))
        self.hold_button.clicked.connect(lambda: self._run_command("hold"))
        self.release_button.clicked.connect(lambda: self._run_command("release"))
        self.stop_button.clicked.connect(lambda: self._run_command("emergency_stop"))
        self.restore_button.clicked.connect(lambda: self._run_command("restore"))
        return panel

    def _build_train_table(self):
        group = QGroupBox("列车运行总览")
        layout = QVBoxLayout(group)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.Interactive)
        for column, width in enumerate((70, 92, 64, 92, 100, 90, 150)):
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(7, QHeaderView.Stretch)
        layout.addWidget(self.table)
        return group

    def set_manager(self, manager: DispatchManager):
        self.manager = manager
        self.line_view.set_manager(manager)
        self.refresh_options()
        self.refresh()

    def refresh_options(self):
        self.start_station_combo.clear()
        for station in sorted(self.manager.track.stations, key=lambda item: item.position):
            self.start_station_combo.addItem(station.name, station.station_id)
        self.plan_combo.clear()
        for plan in self.manager.service_plans.values():
            self.plan_combo.addItem(plan.name, plan.plan_id)

    def refresh(self):
        selected = self.selected_train_id()
        runtimes = tuple(self.manager.trains.values())
        self.table.setRowCount(len(runtimes))
        selected_row = -1
        for row, runtime in enumerate(runtimes):
            plan_name = runtime.service_plan.name if runtime.service_plan else "未设置"
            target = "—"
            if runtime.target_station_id is not None:
                try:
                    target = self.manager.trains.get_station(runtime.target_station_id).name
                except ValueError:
                    target = str(runtime.target_station_id)
            values = (
                runtime.train_id,
                runtime.status.value,
                runtime.direction_label,
                f"{runtime.head_abs:.1f} m",
                f"{runtime.speed_kmh:.1f} km/h",
                target,
                plan_name,
                runtime.blocked_reason or "正常",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (3, 4):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(row, column, item)
            if runtime.train_id == selected:
                selected_row = row
        if selected_row >= 0:
            self.table.selectRow(selected_row)
        elif runtimes:
            self.table.selectRow(0)

        occupied = len(self.manager.occupancy.snapshot)
        locked = len(self.manager.interlocking.locks)
        self.summary.setText(
            f"列车 {len(runtimes)}  |  占用区段 {occupied}  |  锁闭区段 {locked}"
        )
        self.line_view.refresh()

    def selected_train_id(self):
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else None

    def _add_train(self):
        train_id = self.train_id_edit.text().strip()
        station_id = self.start_station_combo.currentData()
        result = self.manager.add_train(train_id, station_id)
        if result.ok:
            self.train_id_edit.clear()
        self._show_result(result)

    def _remove_train(self):
        train_id = self.selected_train_id()
        result = (self.manager.remove_train(train_id) if train_id else
                  DispatchResult(False, "请先选择列车"))
        self._show_result(result)

    def _assign_plan(self):
        train_id = self.selected_train_id()
        plan_id = self.plan_combo.currentData()
        result = (self.manager.assign_plan(train_id, plan_id)
                  if train_id else DispatchResult(False, "请先选择列车"))
        self._show_result(result)

    def _run_command(self, command):
        train_id = self.selected_train_id()
        if not train_id:
            self._show_result(DispatchResult(False, "请先选择列车"))
            return
        result = getattr(self.manager, command)(train_id)
        self._show_result(result)

    def _show_result(self, result: DispatchResult):
        self.feedback.setText(result.message)
        self.feedback.setProperty("result", "ok" if result.ok else "error")
        self.feedback.style().unpolish(self.feedback)
        self.feedback.style().polish(self.feedback)
        self.operation_finished.emit(result.message, result.ok)
        self.refresh()
