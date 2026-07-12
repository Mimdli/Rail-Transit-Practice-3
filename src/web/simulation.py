"""Web 仿真运行时：统一管理状态、命令和仿真时钟。"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import asdict
from typing import Callable, Optional

MAX_CHART_POINTS = 600   # 60s at 10Hz
MAX_ENERGY_POINTS = 1200  # 120s at 10Hz

from src.dispatch import DispatchManager, DispatchResult, ServicePlan
from src.logger.evaluator import Evaluator
from src.logger.recorder import Recorder
from src.network.manager import NetworkManager
from src.power.supply import PowerStatus, PowerSupply
from src.signal.system import SignalAspect, SignalSystem
from src.track.db_loader import DBLoader
from src.track.link_mainline import LinkCoordinateMapper, load_mainline_links
from src.track.loader import TrackLoader
from src.track.semantic_line import build_semantic_line
from src.vehicle.enums import DoorSide, RunningMode
from src.vehicle.environment import MockEnvironment, WeatherType


class SimulationRuntime:
    """保证所有领域对象只在同一个 asyncio 事件循环中访问。"""

    STEP_SECONDS = 0.1

    def __init__(self):
        self._data_source = "database"  # "database" | "demo"
        self.track = DBLoader().load_from_db()
        self.recorder = Recorder()
        self.recorder.start()
        self.signal_system = SignalSystem()
        self.power_supply = PowerSupply()
        self.network = NetworkManager()
        self.semantic_line = build_semantic_line(self.track, self._link_source)
        self.link_mapper = LinkCoordinateMapper(self.track, self._link_source)
        self.weather = WeatherType.DRY
        self.env = MockEnvironment(self.weather, self.track)
        self.dispatch = DispatchManager(
            self.track, self.recorder, self.signal_system)
        self.paused = False
        self.speed_multiplier = 1
        self._task: Optional[asyncio.Task] = None
        self.evaluator = Evaluator()
        self._last_signal_aspects: dict[str, SignalAspect] = {}
        self._overspeed_active: dict[str, bool] = {}
        self._last_status_log_time: float = -1.0
        self._fast_forward_active: bool = False
        self._fast_forward_train: Optional[str] = None
        self._chart_buffers: dict[str, dict] = {}  # train_id -> {field: deque}
        self.scenes: dict = {
            "normal_peak": {
                "name": "正常早高峰", "description": "3 列车 · ATO · 正常供电",
                "weather": "DRY", "power": "NORMAL",
            },
            "segment_conflict": {
                "name": "区段占用冲突", "description": "验证进路拒绝与解释反馈",
                "weather": "DRY", "power": "NORMAL",
            },
            "power_outage": {
                "name": "牵引供电中断", "description": "模拟断电 · 牵引切除",
                "weather": "DRY", "power": "POWER_OFF",
            },
            "low_adhesion": {
                "name": "低黏着运行", "description": "雨雪天气 · 制动距离增加",
                "weather": "RAIN", "power": "NORMAL",
            },
            "comms_loss": {
                "name": "视景通信中断", "description": "UDP 接收超时与告警",
                "weather": "DRY", "power": "NORMAL",
            },
            "custom": {
                "name": "自定义场景", "description": "配置列车、位置与故障组合",
                "weather": "DRY", "power": "NORMAL",
            },
        }
        self.current_scene: str = "normal_peak"
        self._initialize_dispatch()

    def _initialize_dispatch(self):
        stations = sorted(self.track.stations, key=lambda item: item.position)
        if len(stations) < 2:
            return
        station_ids = tuple(station.station_id for station in stations)
        self.dispatch.add_service_plan(ServicePlan(
            "mainline_loop",
            f"{stations[0].name} ⇆ {stations[-1].name}",
            station_ids,
            turnback=True,
            dwell_time=5.0,
        ))
        self.dispatch.add_train("1车", station_ids[0], direction=1)
        self.dispatch.assign_plan("1车", "mainline_loop")

    def _record_signal_changes(self):
        """记录信号显示变化，避免日志中重复写入相同状态。"""
        for sig in self.track.signals:
            aspect = self.signal_system.get_signal_aspect(sig)
            last_aspect = self._last_signal_aspects.get(sig.signal_id)
            if last_aspect is None:
                self._last_signal_aspects[sig.signal_id] = aspect
                continue
            if aspect != last_aspect:
                # 使用第一列车的位置和速度作为上下文
                first_runtime = next(iter(self.dispatch.trains.values()), None)
                speed = first_runtime.controller.head_speed if first_runtime else 0.0
                self.recorder.record(
                    "信号",
                    f"{sig.signal_id} {aspect.value}",
                    sig.position,
                    speed,
                    train_id=first_runtime.train_id if first_runtime else "",
                    source="signal", entity_id=sig.signal_id,
                )
                self._last_signal_aspects[sig.signal_id] = aspect

    def _record_status_snapshots(self):
        """每秒记录一次运行状态快照。"""
        if self._last_status_log_time >= 0 and (
                self.dispatch.sim_time - self._last_status_log_time < 1.0):
            return

        for runtime in self.dispatch.trains.values():
            controller = runtime.controller
            head_abs = runtime.head_abs
            track_limit = self.track.get_speed_limit_at(head_abs)
            effective_limit = self.signal_system.get_effective_speed_limit_for_direction(
                head_abs, controller.direction,
                track_limit, self.track.signals,
            )
            nearest_signal = self.signal_system.get_nearest_signal_for_direction(
                head_abs, controller.direction, self.track.signals,
                look_ahead=float("inf"),
            )
            signal_text = (
                f"{nearest_signal.signal_id} "
                f"{self.signal_system.get_signal_aspect(nearest_signal).value}"
                if nearest_signal else "无前方信号"
            )
            mode = "自动" if controller.running_mode == RunningMode.AUTOMATIC else "手动"
            head_accel = controller.states[0].acceleration if controller.states else 0.0
            description = (
                f"状态快照: 模式 {mode} · 加速度 {head_accel:+.2f} m/s² · "
                f"限速 {effective_limit * 3.6:.0f} km/h · "
                f"信号 {signal_text} · 供电 {self.power_supply.status.value}"
            )
            self.recorder.record(
                "状态", f"{runtime.train_id} · {description}",
                head_abs, controller.head_speed,
                train_id=runtime.train_id, source="simulation",
            )
        self._last_status_log_time = self.dispatch.sim_time

    def _record_overspeed_events(self):
        """检测并记录超速事件。"""
        for runtime in self.dispatch.trains.values():
            controller = runtime.controller
            head_abs = runtime.head_abs
            head_speed = controller.head_speed
            track_limit = self.track.get_speed_limit_at(head_abs)
            effective_limit = self.signal_system.get_effective_speed_limit_for_direction(
                head_abs, controller.direction,
                track_limit, self.track.signals,
            )
            overspeed = head_speed > effective_limit + 0.5
            was_overspeed = self._overspeed_active.get(runtime.train_id, False)
            if overspeed and not was_overspeed:
                self.recorder.record(
                    "超速",
                    f"超速: {head_speed * 3.6:.1f} km/h",
                    head_abs, head_speed,
                    train_id=runtime.train_id,
                    source="protection", severity="WARNING",
                )
            self._overspeed_active[runtime.train_id] = overspeed

    def _update_evaluator(self):
        """更新评价器的最高速度。"""
        for runtime in self.dispatch.trains.values():
            self.evaluator.update_max_speed(runtime.controller.head_speed)

    def _feed_chart_buffers(self):
        """每仿真步向环形缓冲区写入图表数据。"""
        for runtime in self.dispatch.trains.values():
            tid = runtime.train_id
            if tid not in self._chart_buffers:
                self._chart_buffers[tid] = {
                    "speedForce": deque(maxlen=MAX_CHART_POINTS),
                    "energy": deque(maxlen=MAX_ENERGY_POINTS),
                }
            ctrl = runtime.controller
            report = ctrl.last_report
            t = self.dispatch.sim_time
            limit = self.track.get_speed_limit_at(runtime.head_abs) * 3.6
            self._chart_buffers[tid]["speedForce"].append({
                "t": round(t, 2),
                "speedKmh": round(runtime.speed_kmh, 2),
                "couplerKn": round(report.max_coupler_force / 1000, 1) if report else 0,
                "limitKmh": round(limit, 1),
            })
            es = ctrl.energy_last_step
            if es:
                self._chart_buffers[tid]["energy"].append({
                    "t": round(t, 2),
                    "tractionKw": round(es.traction_power_kw, 1),
                    "regenKw": round(es.regen_power_kw, 1),
                    "auxKw": round(es.aux_energy_j / es.dt / 1000, 1) if es.dt > 0 else 0,
                    "netKw": round(es.net_energy_j / es.dt / 1000, 1) if es.dt > 0 else 0,
                    "cumTractionKwh": round(ctrl.energy_traction_kwh, 3),
                    "cumRegenKwh": round(ctrl.energy_regen_kwh, 3),
                    "cumNetKwh": round(ctrl.energy_net_kwh, 3),
                })

    async def start(self):
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="web-simulation")

    async def stop(self):
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.network.stop()
        self.recorder.close()

    async def _run(self):
        while True:
            if not self.paused:
                dt = self.STEP_SECONDS * self.speed_multiplier
                if not self.power_supply.can_traction():
                    for runtime in self.dispatch.trains.values():
                        runtime.controller.set_throttle(0.0)
                if self._fast_forward_active:
                    self._step_fast_forward(dt)
                else:
                    self.dispatch.step(dt)
                    self._record_signal_changes()
                    self._record_status_snapshots()
                    self._record_overspeed_events()
                    self._update_evaluator()
                    self._feed_chart_buffers()
                self.power_supply.step(dt)
                self.recorder.step(dt)
            await asyncio.sleep(self.STEP_SECONDS)

    def _step_fast_forward(self, dt: float):
        """快进模式：批量步进直到停止条件满足。"""
        runtime = self.dispatch.trains.get(self._fast_forward_train or "")
        if runtime is None:
            self._fast_forward_active = False
            return
        batch_size = 500
        for _ in range(batch_size):
            self.dispatch.step(dt)
            self._record_signal_changes()
            self._record_overspeed_events()
            self._update_evaluator()
            self._feed_chart_buffers()
            if self._check_fast_forward_stop(runtime):
                self._fast_forward_active = False
                self._fast_forward_train = None
                self.recorder.record(
                    "操作", "Web 快进完成",
                    runtime.head_abs, runtime.controller.head_speed,
                    train_id=runtime.train_id, source="web-ats",
                )
                break

    def command(self, action: Callable[[], DispatchResult]) -> dict:
        """把领域命令的统一结果转换成稳定 JSON 结构。"""
        result = action()
        return {"ok": result.ok, "message": result.message}

    def record_web_command(self, train_id: str, command: str,
                           result: dict):
        """记录 Web 调度命令及执行结果，便于按列车追溯。"""
        runtime = self.dispatch.trains.get(train_id)
        self.recorder.record(
            "Web命令",
            f"{command}: {result['message']}",
            runtime.head_abs if runtime else 0.0,
            runtime.controller.head_speed if runtime else 0.0,
            train_id=train_id,
            source="web-ats",
            severity="INFO" if result["ok"] else "WARNING",
            entity_id=command,
        )

    def set_power(self, value: str) -> dict:
        try:
            status = PowerStatus[value.upper()]
        except KeyError:
            return {"ok": False, "message": f"未知供电状态: {value}"}
        self.power_supply.set_status(status)
        self.recorder.record("供电", f"供电状态切换为{status.value}")
        return {"ok": True, "message": f"供电状态已切换为{status.value}"}

    def door_command(self, train_id: str, side: str) -> dict:
        """车门控制命令。"""
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        controller = runtime.controller
        if side == "left":
            ok = controller.open_door(DoorSide.LEFT)
            label = "开左门"
        elif side == "right":
            ok = controller.open_door(DoorSide.RIGHT)
            label = "开右门"
        elif side == "close":
            controller.close_door()
            ok = True
            label = "关门"
        else:
            return {"ok": False, "message": f"未知车门命令: {side}"}
        if ok:
            self.recorder.record(
                "操作", f"Web {label}",
                runtime.head_abs, controller.head_speed,
                train_id=train_id, source="web-ats",
            )
            return {"ok": True, "message": f"{train_id} {label}成功"}
        return {"ok": False, "message": f"{train_id} {label}失败（列车未停稳）"}

    def set_running_mode(self, train_id: str, mode: str) -> dict:
        """切换列车驾驶模式。"""
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        controller = runtime.controller
        if mode == "manual":
            if controller.running_mode == RunningMode.MANUAL:
                return {"ok": True, "message": "已在手动模式"}
            controller.close_door()
            runtime.auto_drive.reset_state()
            controller.set_running_mode(RunningMode.MANUAL)
            controller.command_stop(brake_level=0.4)
            self.recorder.record(
                "操作", "Web 切换手动模式",
                runtime.head_abs, controller.head_speed,
                train_id=train_id, source="web-ats",
            )
            return {"ok": True, "message": f"{train_id} 已切换至手动驾驶"}
        elif mode == "auto":
            if controller.running_mode == RunningMode.AUTOMATIC:
                return {"ok": True, "message": "已在自动模式"}
            controller.close_door()
            head_abs = runtime.head_abs
            next_station = runtime.auto_drive.find_next_station(head_abs)
            if next_station is None:
                return {"ok": False, "message": "切换自动失败: 无前方车站"}
            target = runtime.track_adapter.from_absolute(next_station.position)
            runtime.auto_drive.set_target(target)
            controller.set_running_mode(RunningMode.AUTOMATIC)
            self.recorder.record(
                "操作", f"Web 切换自动模式 → {next_station.name}",
                head_abs, controller.head_speed,
                train_id=train_id, source="web-ats",
            )
            return {"ok": True, "message": f"{train_id} 已切换至自动驾驶 → {next_station.name}"}
        else:
            return {"ok": False, "message": f"未知驾驶模式: {mode}"}

    def set_weather(self, weather_name: str) -> dict:
        """切换天气/环境状态。"""
        try:
            new_weather = WeatherType[weather_name.upper()]
        except KeyError:
            return {"ok": False, "message": f"未知天气类型: {weather_name}"}
        self.weather = new_weather
        self.env = MockEnvironment(self.weather, self.track)
        # 更新所有列车控制器的环境引用
        for runtime in self.dispatch.trains.values():
            runtime.controller.env = self.env
        self.recorder.record("环境", f"天气切换为{self.weather.value}")
        return {"ok": True, "message": f"天气已切换为{self.weather.value}"}

    def apply_control_level(self, train_id: str, level_name: str) -> dict:
        """设置司控器级位（仅手动模式可用）。"""
        from src.vehicle.enums import ControlLevel
        from src.dispatch.models import TrainStatus

        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        controller = runtime.controller
        if controller.running_mode != RunningMode.MANUAL:
            return {"ok": False, "message": "控制级位仅在手动模式下可用，请先切换到手动驾驶"}

        # 激活直接控制（与桌面端 _activate_direct_control 一致）
        runtime.held = False
        runtime.emergency = False
        runtime.blocked_reason = ""
        runtime.status = TrainStatus.MANUAL
        if hasattr(controller, 'interlock') and controller.interlock:
            controller.interlock.emergency_brake_required = False
            controller.interlock.traction_permitted = True

        try:
            level = ControlLevel[level_name.upper()]
        except KeyError:
            return {"ok": False, "message": f"未知控制级位: {level_name}"}

        controller.apply_control_level(level)
        level_labels = {
            "FULL_TRACTION": "P3 全力牵引", "MEDIUM_TRACTION": "P2 中速牵引",
            "LOW_TRACTION": "P1 低速牵引", "COAST": "惰行",
            "SERVICE_BRAKE": "B1 常用制动", "FULL_BRAKE": "B2 全制动",
            "EMERGENCY_BRAKE": "EB 紧急制动",
        }
        label = level_labels.get(level.name, level.name)
        self.recorder.record(
            "操作", f"Web 手柄 → {label}",
            runtime.head_abs, controller.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"手柄已设置为 {label}", "level": level.name}

    def set_load_level(self, train_id: str, level_name: str) -> dict:
        """设置列车载荷等级。"""
        from src.vehicle.enums import LoadLevel

        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        try:
            level = LoadLevel[level_name.upper()]
        except KeyError:
            return {"ok": False, "message": f"未知载荷等级: {level_name}"}
        runtime.controller.set_load_level(level)
        labels = {"AW0": "空载", "AW1": "满座", "AW2": "定员", "AW3": "超载"}
        label = labels.get(level.name, level.name)
        self.recorder.record(
            "操作", f"Web 载荷 → {label}",
            runtime.head_abs, runtime.controller.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"载荷已设为 {label}"}

    def set_dwell_time(self, train_id: str, seconds: float) -> dict:
        """设置列车停站时间。"""
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        if seconds < 1 or seconds > 300:
            return {"ok": False, "message": "停站时间须在 1-300 秒之间"}
        runtime.auto_drive.dwell_time = seconds
        self.recorder.record(
            "操作", f"Web 停站时间 → {seconds:.0f}s",
            runtime.head_abs, runtime.controller.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"停站时间已设为 {seconds:.0f}s"}

    def fast_forward_to_next_station(self, train_id: str) -> dict:
        """跳站快进：设定 ATO 目标为下一站并加速推进。"""
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        head_abs = runtime.head_abs
        next_station = self.track.get_nearest_station_ahead(head_abs)
        if next_station is None:
            return {"ok": False, "message": "无前方车站可跳转"}
        controller = runtime.controller
        target = runtime.track_adapter.from_absolute(next_station.position)
        runtime.auto_drive.set_target(target)
        if controller.running_mode != RunningMode.AUTOMATIC:
            controller.close_door()
            controller.set_running_mode(RunningMode.AUTOMATIC)
        self._fast_forward_train = train_id
        self._fast_forward_active = True
        self.recorder.record(
            "操作", f"Web 快进至 {next_station.name} ({next_station.position:.0f}m)",
            head_abs, controller.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"快进至 {next_station.name}"}

    def _check_fast_forward_stop(self, runtime) -> bool:
        """检查快进停止条件。"""
        from src.signal.system import SignalAspect
        controller = runtime.controller
        head_abs = runtime.head_abs
        head_speed = controller.head_speed
        # 已到站进入停站
        if runtime.auto_drive.station_phase.name == "DWELL":
            return True
        # 已停稳且接近目标
        distance = runtime.auto_drive.distance_to_target
        if controller.is_stopped and distance is not None and distance < 5.0:
            return True
        # 超速
        track_limit = self.track.get_speed_limit_at(head_abs)
        effective_limit = self.signal_system.get_effective_speed_limit(
            head_abs, track_limit, self.track.signals,
        )
        if head_speed > effective_limit + 3.0:
            controller.emergency_brake()
            return True
        # 到线路终点
        if head_abs >= self.track.total_length() - 10.0:
            controller.emergency_brake()
            return True
        # 闯红灯
        for sig in self.track.signals:
            aspect = self.signal_system.get_signal_aspect(sig)
            if (aspect == SignalAspect.RED and head_abs > sig.position
                    and head_abs - sig.position < 50):
                return True
        return False

    def apply_scene(self, scene_id: str) -> dict:
        """应用预设场景。"""
        scene = self.scenes.get(scene_id)
        if scene is None:
            return {"ok": False, "message": f"未知场景: {scene_id}"}
        self.current_scene = scene_id
        # 设置天气
        self.set_weather(scene["weather"])
        # 设置供电
        self.set_power(scene["power"])
        self.recorder.record(
            "场景", f"应用场景: {scene['name']}",
            source="web-ats",
        )
        return {"ok": True, "message": f"已应用场景: {scene['name']}"}

    # ── Phase 4: 图表与力数据 ─────────────────────────────────

    def get_chart_speed_force(self, train_id: str) -> dict:
        buf = self._chart_buffers.get(train_id, {})
        return {"ok": True, "data": list(buf.get("speedForce", []))}

    def get_chart_energy(self, train_id: str) -> dict:
        buf = self._chart_buffers.get(train_id, {})
        return {"ok": True, "data": list(buf.get("energy", []))}

    def get_force_table(self, train_id: str) -> dict:
        rt = self.dispatch.trains.get(train_id)
        if rt is None:
            return {"ok": False, "message": "列车不存在"}
        report = rt.controller.last_report
        if report is None:
            return {"ok": True, "cars": []}
        cars = []
        for c in report.cars:
            has_slip = c.traction_limited
            has_slide = c.brake_limited
            adhesion = "空转" if has_slip else ("滑行" if has_slide else "正常")
            cars.append({
                "index": c.car_index,
                "velocityKmh": round(c.velocity * 3.6, 2),
                "tractiveKn": round(c.tractive_force / 1000, 1),
                "brakeKn": round(abs(c.brake_force) / 1000, 1),
                "electricBrakeKn": round(abs(c.electric_brake_force) / 1000, 1),
                "frictionBrakeKn": round(abs(c.friction_brake_force) / 1000, 1),
                "davisKn": round(abs(c.davis_resistance) / 1000, 1),
                "gradeKn": round(c.grade_resistance / 1000, 1),
                "couplerFrontKn": round(c.coupler_force_front / 1000, 1),
                "couplerRearKn": round(c.coupler_force_rear / 1000, 1),
                "netForceKn": round(c.net_force / 1000, 1),
                "adhesion": adhesion,
            })
        return {"ok": True, "cars": cars}

    # ── Phase 5: 编组配置 ─────────────────────────────────────

    def apply_consist_preset(self, train_id: str, preset: str) -> dict:
        from src.common.consist import CONSIST_4M2T, CONSIST_6M0T, CONSIST_1M4T
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        presets = {"4M2T": CONSIST_4M2T, "6M0T": CONSIST_6M0T, "1M4T": CONSIST_1M4T}
        consist = presets.get(preset)
        if consist is None:
            return {"ok": False, "message": f"未知编组预设: {preset}"}
        runtime.controller.replace_consist(consist, runtime.track_adapter)
        self.recorder.record(
            "操作", f"Web 编组切换 → {preset} ({consist.motor_count}M{consist.trailer_count}T)",
            runtime.head_abs, runtime.controller.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"编组已切换为 {preset}"}

    def get_consist_config(self, train_id: str) -> dict:
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        ctrl = runtime.controller
        cars = []
        for i, car in enumerate(ctrl.consist):
            cars.append({
                "index": i,
                "type": "M" if car.is_motor else "T",
                "massKg": round(car.mass, 1),
                "lengthM": round(car.length, 3),
                "davisA": car.davis_A,
                "davisB": car.davis_B,
                "davisC": car.davis_C,
                "maxTractionN": car.max_traction_force,
                "maxServiceBrakeN": car.max_service_brake_force,
                "maxEmergencyBrakeN": car.max_emergency_brake_force,
                "aw0MassKg": round(car.aw0_mass, 1),
                "aw1MassKg": round(car.aw1_mass, 1),
                "aw2MassKg": round(car.aw2_mass, 1),
                "aw3MassKg": round(car.aw3_mass, 1),
            })
        return {
            "ok": True,
            "cars": cars,
            "motorCount": ctrl.consist.motor_count,
            "trailerCount": ctrl.consist.trailer_count,
            "totalMassKg": round(ctrl.consist.total_mass, 1),
            "totalLengthM": round(ctrl.consist.total_length, 2),
        }

    # ── Phase 6: 交路选择与车站跳转 ───────────────────────────

    def set_route(self, train_id: str, route_id: str) -> dict:
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        # 从调度获取可用交路
        plans = self.dispatch.service_plans
        if route_id not in plans:
            available = list(plans.keys())
            return {"ok": False, "message": f"未知交路: {route_id}，可用: {', '.join(available)}"}
        self.dispatch.assign_plan(train_id, route_id)
        self.recorder.record(
            "操作", f"Web 交路切换 → {route_id}",
            runtime.head_abs, runtime.controller.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"交路已切换为 {route_id}"}

    def get_available_routes(self, train_id: str) -> dict:
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        plans = self.dispatch.service_plans
        routes = [
            {
                "id": pid,
                "name": p.name,
                "stations": list(p.station_ids),
                "turnback": p.turnback,
                "dwellTime": p.dwell_time,
            }
            for pid, p in plans.items()
        ]
        current = runtime.service_plan.name if runtime.service_plan else None
        return {"ok": True, "routes": routes, "current": current}

    def jump_to_station(self, train_id: str, station_id: str) -> dict:
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车 {train_id} 不存在"}
        # 查找车站
        station = next(
            (s for s in self.track.stations if str(s.station_id) == str(station_id)),
            None,
        )
        if station is None:
            return {"ok": False, "message": f"车站 {station_id} 不存在"}
        # 解析位置
        target = runtime.track_adapter.from_absolute(station.position - 50.0)
        ctrl = runtime.controller
        ctrl.reset_states(
            start_segment_id=target.segment_id,
            start_offset=target.offset,
        )
        ctrl.reset_energy()
        runtime.auto_drive.reset_state()
        # 设置 ATO 目标为前方第一个车站
        next_station = self.track.get_nearest_station_ahead(station.position)
        if next_station:
            runtime.auto_drive.set_target(
                runtime.track_adapter.from_absolute(next_station.position)
            )
            runtime.target_station_id = next_station.station_id
        self.recorder.record(
            "操作", f"Web 跳站 → {station.name} ({station.position:.0f}m)",
            runtime.head_abs, ctrl.head_speed,
            train_id=train_id, source="web-ats",
        )
        return {"ok": True, "message": f"已跳转到 {station.name}"}

    # ── Phase 7: 网络开关 ─────────────────────────────────────

    def toggle_network(self, enabled: bool) -> dict:
        if enabled:
            self.network.start()
            return {"ok": True, "message": "网络通信已开启"}
        else:
            self.network.stop()
            return {"ok": True, "message": "网络通信已关闭"}

    # ── 数据源切换 ─────────────────────────────────────────

    def switch_track_source(self, source: str) -> dict:
        """切换线路数据源：database（数据库）或 demo（演示数据）。"""
        if source not in ("database", "demo"):
            return {"ok": False, "message": f"未知数据源: {source}，可选 database / demo"}
        if source == self._data_source:
            return {"ok": True, "message": f"已在 {source} 数据源，无需切换"}

        # 加载新数据
        if source == "demo":
            new_track = TrackLoader().load_demo_data()
            label = "演示数据 (双链 8 段 1000m × 2)"
        else:
            try:
                new_track = DBLoader().load_from_db()
                label = "数据库线路数据"
            except Exception as e:
                return {"ok": False, "message": f"加载数据库数据失败: {e}"}

        # 替换 track 并重建依赖
        self.track = new_track
        # 先更新 _data_source，再重建依赖（_link_source 依赖 _data_source）
        old_source = self._data_source
        self._data_source = source
        try:
            self.semantic_line = build_semantic_line(self.track, self._link_source)
            self.link_mapper = LinkCoordinateMapper(self.track, self._link_source)
        except Exception:
            self._data_source = old_source
            raise
        self.env = MockEnvironment(self.weather, self.track)

        # 重建 dispatch（清空旧列车和联锁状态）
        self.dispatch = DispatchManager(
            self.track, self.recorder, self.signal_system)
        self.signal_system = SignalSystem()
        self.dispatch = DispatchManager(
            self.track, self.recorder, self.signal_system)
        self._chart_buffers.clear()
        self._overspeed_active.clear()
        self._last_signal_aspects.clear()
        self._fast_forward_active = False
        self._fast_forward_train = None

        # 重新初始化列车和交路
        self._initialize_dispatch()

        self.recorder.record("系统", f"数据源切换 → {label}", source="web-ats")
        return {"ok": True, "message": f"数据源已切换为 {label}",
                "source": source, "label": label}

    @property
    def data_source(self) -> str:
        return self._data_source

    @property
    def _link_source(self) -> str:
        """当前数据源对应的 Link 公里标键名。"""
        return "demo" if self._data_source == "demo" else "directions"

    # ── 回放 ─────────────────────────────────────────────────

    def replay_data(self) -> dict:
        """返回回放所需的全部事件和评价数据。"""
        return {
            "events": [{"id": i, **asdict(e)}
                       for i, e in enumerate(self.recorder.events)],
            "evaluation": self.evaluator.evaluate(self.recorder),
            "simTime": round(self.dispatch.sim_time, 2),
        }

    def snapshot(self) -> dict:
        occupancy = self.dispatch.occupancy.snapshot
        locks = self.dispatch.interlocking.locks
        trains = [self._serialize_train(runtime)
                  for runtime in self.dispatch.trains.values()]
        signals = [
            {
                "id": signal.signal_id,
                "segmentId": signal.seg_id,
                "offset": signal.offset,
                "direction": signal.direction,
                "aspect": self.signal_system.get_signal_aspect(signal).name,
            }
            for signal in self.track.signals
        ]
        events = [
            {"id": index, **asdict(event)}
            for index, event in enumerate(self.recorder.events[-120:])
        ]
        return {
            "type": "snapshot",
            "simTime": round(self.dispatch.sim_time, 2),
            "paused": self.paused,
            "speedMultiplier": self.speed_multiplier,
            "trains": trains,
            "occupancy": {str(key): sorted(value)
                          for key, value in occupancy.items()},
            "locks": {str(key): value for key, value in locks.items()},
            "signals": signals,
            "power": {
                "status": self.power_supply.status.name,
                "label": self.power_supply.status.value,
                "tractionCapability": self.power_supply.get_traction_limit(),
            },
            "weather": {
                "type": self.weather.name,
                "label": self.weather.value,
                "adhesion": round(self.env.get_adhesion_coefficient(), 4),
            },
            "network": self.network.connection_status,
            "dataSource": self._data_source,
            "stations": [
                {"id": item.station_id, "name": item.name,
                 "position": item.position}
                for item in sorted(self.track.stations,
                                   key=lambda station: station.position)
            ],
            "line": self._serialize_line(),
            "trackSummary": {
                "segmentCount": len(self.track.segments),
                "stationCount": len(self.track.stations),
                "signalCount": len(self.track.signals),
                "switchCount": getattr(self.track, "switches_count", 0),
                "totalLength": round(self.track.total_length(), 0),
            },
            "events": events,
            "evaluation": self.evaluator.evaluate(self.recorder),
        }

    def _serialize_line(self) -> dict:
        """输出与桌面运营线路图一致的 Link 主线和语义分支。"""
        links = load_mainline_links(self._link_source)
        return {
            "totalLength": self.semantic_line.total_length,
            "stations": [asdict(station)
                         for station in self.semantic_line.stations],
            "directions": {
                direction: [asdict(link) for link in items]
                for direction, items in links.items()
            },
            "branches": [asdict(branch)
                         for branch in self.semantic_line.branches],
        }

    def _serialize_train(self, runtime) -> dict:
        controller = runtime.controller
        head = controller.states[0].position if controller.states else None
        link_position = (self.link_mapper.to_link_position(head)
                         if head is not None else None)
        limit = (runtime.track_adapter.get_speed_limit(head) * 3.6
                 if head is not None else 0.0)
        target = next(
            (station for station in self.track.stations
             if station.station_id == runtime.target_station_id), None)
        plan = runtime.service_plan
        report = controller.last_report
        energy_summary = controller.energy_summary()
        head_abs = runtime.head_abs
        station_phase = runtime.auto_drive.station_phase.name if runtime.auto_drive else "CRUISING"

        # ── 力分量 ──────────────────────────────────────────
        electric_brake_kn = 0.0
        friction_brake_kn = 0.0
        max_coupler_kn = 0.0
        adhesion_status = "ok"
        if report and report.cars:
            electric_brake_kn = round(
                sum(abs(c.electric_brake_force) for c in report.cars) / 1000, 1)
            friction_brake_kn = round(
                sum(abs(c.friction_brake_force) for c in report.cars) / 1000, 1)
            max_coupler_kn = round(report.max_coupler_force / 1000, 1)
            has_slip = any(c.traction_limited for c in report.cars)
            has_slide = any(c.brake_limited for c in report.cars)
            if has_slip and has_slide:
                adhesion_status = "空转+滑行"
            elif has_slip:
                adhesion_status = "空转"
            elif has_slide:
                adhesion_status = "滑行"
            else:
                adhesion_status = "正常"

        # ── 前方信号序列 ────────────────────────────────────
        signal_sequence = []
        all_signals = sorted(self.track.signals, key=lambda s: s.position)
        ahead_signals = [s for s in all_signals if s.position > head_abs - 5]
        for sig in ahead_signals[:5]:
            dist = sig.position - head_abs
            aspect = self.signal_system.get_signal_aspect(sig)
            signal_sequence.append({
                "id": sig.signal_id,
                "aspect": aspect.name,
                "aspectLabel": aspect.value,
                "distance": round(dist, 1),
                "position": round(sig.position, 1),
            })

        return {
            "id": runtime.train_id,
            "status": runtime.status.value,
            "direction": runtime.direction_label,
            "directionCode": controller.direction,
            "speedKmh": round(runtime.speed_kmh, 2),
            "headPosition": round(head_abs, 3),
            "linkPosition": (round(link_position, 3)
                             if link_position is not None else None),
            "segmentId": head.segment_id if head else None,
            "offset": round(head.offset, 3) if head else None,
            "speedLimitKmh": round(limit, 1),
            "targetStationId": runtime.target_station_id,
            "targetStation": target.name if target else None,
            "targetDistance": (round(abs(target.position - head_abs), 1)
                               if target else None),
            "held": runtime.held,
            "emergency": runtime.emergency,
            "blockedReason": runtime.blocked_reason,
            "servicePlan": plan.name if plan else None,
            "throttle": round(controller.throttle, 3),
            "brakeLevel": round(controller.brake_level, 3),
            "throttlePct": round(controller.throttle * 100),
            "brakePct": round(controller.brake_level * 100),
            "controlLevel": (controller.traction._current_level.name
                             if hasattr(controller.traction, '_current_level')
                             else "COAST"),
            "acceleration": (round(controller.states[0].acceleration, 3)
                             if controller.states else 0.0),
            "tractiveForceKn": (round(report.total_tractive_force / 1000, 1)
                                if report else 0.0),
            "brakeForceKn": (round(sum(abs(car.brake_force)
                                        for car in report.cars) / 1000, 1)
                             if report else 0.0),
            # 新增力分量
            "electricBrakeKn": electric_brake_kn,
            "frictionBrakeKn": friction_brake_kn,
            "maxCouplerForceKn": max_coupler_kn,
            "adhesionStatus": adhesion_status,
            # 新增能耗指标
            "energyKwh": round(controller.energy_net_kwh, 3),
            "energyTractionKwh": round(controller.energy_traction_kwh, 3),
            "energyRegenKwh": round(controller.energy_regen_kwh, 3),
            "energyAuxKwh": round(controller.energy_aux_kwh, 3),
            "energyFrictionLossKwh": round(controller.energy_friction_loss_kwh, 3),
            "regenRatio": round(controller.energy_regen_ratio, 3),
            "kwhPerCarKm": round(energy_summary.kwh_per_car_km, 4),
            "kwhPer1000tKm": round(energy_summary.kwh_per_1000_ton_km, 4),
            "tripDistanceM": round(energy_summary.trip_distance_m, 1),
            # 新增线路信息
            "gradientPerMille": round(self.track.get_gradient_at(head_abs), 2),
            "signalSequence": signal_sequence,
            "stationPhase": station_phase,
            "doors": {
                "left": controller.left_door_open,
                "right": controller.right_door_open,
            },
            "carCount": len(controller.consist),
            "motorCount": controller.consist.motor_count,
            "trailerCount": controller.consist.trailer_count,
            "totalMassTons": round(controller.consist.total_mass / 1000, 1),
            "runningMode": controller.running_mode.name,
        }
