"""Web 仿真运行时：统一管理状态、命令和仿真时钟。"""

from __future__ import annotations

import asyncio
from collections import deque
from collections import deque
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone
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
from src.track.ats_layout import load_ats_layout
from src.track.link_mainline import LinkCoordinateMapper, load_mainline_links
from src.track.loader import TrackLoader
from src.common.track_position import TrackPosition
from src.common.consist import CONSIST_1M4T, CONSIST_4M2T, CONSIST_6M0T
from src.track.semantic_line import build_semantic_line
from src.vehicle.enums import DoorSide, RunningMode
from src.vehicle.environment import MockEnvironment, WeatherType
from src.vehicle.environment import WeatherType
from src.vehicle.enums import ControlLevel, DoorSide, LoadLevel, RunningMode
from src.dispatch.models import TrainStatus


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
        self.ats_switches, self.ats_signals = load_ats_layout()
        self.weather = WeatherType.DRY
        self.env = MockEnvironment(self.weather, self.track)
        self.dispatch = DispatchManager(
            self.track, self.recorder, self.signal_system)
        self.paused = False
        self.speed_multiplier = 1
        self._snapshot_sequence = 0
        self.active_scenario = "normal"
        self.network_fault_injected = False
        self.replay_frames: deque[dict] = deque(maxlen=1200)
        self._last_replay_sample = -1.0
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
        self.network_started = False
        self.cab_started = False
        self._fast_forward: Optional[dict] = None
        self._plc_output_counter = 0
        self.plc_output_state = {
            "indicator_hv_contactor": False,
            "indicator_brake_release": False,
            "indicator_door_closed": True,
            "indicator_network_fault": False,
            "mode_ato_available": False,
            "mode_ato_active": False,
            "mode_ar": False,
            "btn_emergency_brake": False,
            "btn_forced_release": False,
            "btn_forced_pump": False,
            "btn_emergency_command": False,
            "btn_parking_brake": False,
            "btn_open_left": False,
            "btn_open_right": False,
            "btn_close_left": False,
            "btn_close_right": False,
            "tag17": 0,
        }
        self.cab_display_state = {
            "speed": 0.0,
            "acceleration": 0.0,
            "speed_limit": 80.0,
            "run_mode": 0,
            "run_dir": 0,
            "power_pull": 0,
            "net_pressure": 750.0,
            "curr_station": 1,
            "next_station": 2,
            "end_station": 1,
            "power_state": 1,
            "door_states": [0, 0, 0, 0],
            "has_power": True,
            # 信号屏专有字段
            "mode": 5,
            "pull_switch": 0,
            "pull_state": 0,
            "brake_state": 0,
            "urgency_stop": 0,
            "event_id": 0,
            "sig_state": 0,
            "train_no": 1,
            "next_station_dist": 0.0,
        }
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
        # 下行列车：从首站出发，沿递增里程运行 (A→B→C→D)
        self.dispatch.add_train("1车", station_ids[0], direction=1)
        self.dispatch.assign_plan("1车", "mainline_loop")
        # 上行列车：从末站出发，沿递减里程运行 (D→C→B→A)
        if len(station_ids) >= 2:
            self.dispatch.add_train("2车", station_ids[-1], direction=-1)
            self.dispatch.assign_plan("2车", "mainline_loop")

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

    def network_connect(self) -> dict:
        """启动所有网络通信模块"""
        if self.network_started:
            return {"ok": True, "message": "网络已连接"}
        self._setup_network_sources()
        self.network.start()
        self.network_started = True
        self.recorder.record("网络", "Web ATS 启动联调网络连接", severity="INFO")
        return {"ok": True, "message": "网络连接已启动"}

    def _setup_network_sources(self):
        """连接仿真数据到网络模块的数据源"""
        from src.signal.system import SignalAspect

        # 1. 车辆UDP：发送所有列车位置
        def _vehicle_source():
            trains = []
            for runtime in self.dispatch.trains.values():
                ctrl = runtime.controller
                if ctrl.states:
                    s = ctrl.states[0]
                    trains.append((s.acceleration, ctrl.head_speed, runtime.head_abs))
                else:
                    trains.append((0.0, 0.0, 0.0))
            # 补齐20列车（UDP协议固定20槽）
            while len(trains) < 20:
                trains.append((0.0, 0.0, 0.0))
            return trains[:20]

        self.network.set_vehicle_send_source(_vehicle_source)

        # 2. 信号网关：发送道岔/信号状态
        def _signal_source():
            switches = []   # [(id, state)] — 暂不发送道岔
            _aspect_map = {
                SignalAspect.RED: 0x01,
                SignalAspect.YELLOW: 0x02,
                SignalAspect.GREEN: 0x04,
            }
            signals = [
                (s.signal_id, _aspect_map.get(
                    self.signal_system.get_signal_aspect(s), 0x00))
                for s in self.track.signals
            ]
            return (switches, signals[:20])

        self.network.set_signal_send_source(_signal_source)

        # 3. 信号网关接收回调：记录日志
        _last_signal_data = [None]

        def _on_signal_recv(data: bytes):
            _last_signal_data[0] = data
            self.recorder.record("信号网关", f"收到 {len(data)} 字节", severity="INFO")

        self.network.set_signal_recv_callback(_on_signal_recv)

        # 4. PLC 接收回调：将司机台物理操作映射到列车控制
        def _on_plc_recv(data: dict):
            # 控制首列可用列车（模拟单一司机台控制一辆车）
            runtimes = list(self.dispatch.trains.values())
            if not runtimes:
                self.recorder.record("PLC", f"无可用列车: {str(data)}",
                                     severity="WARNING")
                return
            runtime = runtimes[0]
            ctrl = runtime.controller

            # 牵引极位 0-100 → throttle 0.0-1.0
            traction = data.get("traction_level", 0)
            ctrl.traction.set_throttle(traction / 100.0, bypass_mode_check=True)

            # 制动极位 0-100 → brake 0.0-1.0
            brake = data.get("brake_level", 0)
            ctrl.traction.set_brake(brake / 100.0, bypass_mode_check=True)

            # 方向手柄
            if data.get("dir_forward"):
                ctrl.direction = 1
            elif data.get("dir_backward"):
                ctrl.direction = -1

            # 紧急制动按钮
            if data.get("btn_emergency_brake"):
                ctrl.emergency_brake()
                runtime.emergency = True
                runtime.status = TrainStatus.EMERGENCY_STOP

            # 车门控制
            if data.get("btn_open_left"):
                ctrl.open_door(DoorSide.LEFT)
            elif data.get("btn_close_left"):
                ctrl.close_door()
            if data.get("btn_open_right"):
                ctrl.open_door(DoorSide.RIGHT)
            elif data.get("btn_close_right"):
                ctrl.close_door()

            # ATO 启动按钮
            if data.get("btn_ato_start"):
                ctrl.set_running_mode(RunningMode.AUTOMATIC)

            # 记录关键操作
            parts = [f"牵引{traction}", f"制动{brake}"]
            if data.get("dir_forward"):
                parts.append("方向前")
            elif data.get("dir_backward"):
                parts.append("方向后")
            if data.get("btn_emergency_brake"):
                parts.append("紧急制动")
            self.recorder.record(
                "PLC",
                f"{runtime.train_id} {' '.join(parts)}",
                runtime.head_abs, ctrl.head_speed,
                train_id=runtime.train_id, source="plc",
                severity="INFO",
            )

        self.network.set_plc_recv_callback(_on_plc_recv)

        # 5. 视景系统数据源：使用默认内部数据生成
        #    VisionUDPClient 内部已有 TCMS2VIEW 数据生成逻辑，无需手动设置

        # 6. 司机台显示屏数据源
        self._setup_cab_display_source()
        # 6. 司机台显示屏数据源：接入仿真列车状态
        self._feed_cab_display()

    def _feed_cab_display(self):
        """为司机台显示屏准备网络屏和信号屏数据。

        参照桌面版 _feed_cab_display()，从首列车的控制器状态构造
        网络屏 (572B → 总控:8888) 和信号屏 (68B → 总控:9999) 的数据字典。
        """
        runtimes = list(self.dispatch.trains.values())
        if not runtimes:
            return
        runtime = runtimes[0]
        ctrl = runtime.controller
        head_abs = runtime.head_abs
        head_speed = ctrl.head_speed
        head_accel = ctrl.states[0].acceleration if ctrl.states else 0.0

        # 限速
        track_limit = (runtime.track_adapter.get_speed_limit(
            ctrl.states[0].position) * 3.6 if ctrl.states else 0.0)

        # 当前/下一站
        stations = sorted(self.track.stations, key=lambda s: s.position)
        curr_station_id = 0
        next_station_id = 0
        next_station_dist = 0.0
        for i, st in enumerate(stations):
            if st.position <= head_abs:
                curr_station_id = st.station_id
                if i + 1 < len(stations):
                    next_st = stations[i + 1]
                    next_station_id = next_st.station_id
                    next_station_dist = max(0.0, next_st.position - head_abs)

        # 车门状态 (6 节车厢，每位表示一扇门)
        door_states = self._build_door_states(ctrl)

        # 网络屏数据源
        def _net_source():
            return {
                "speed": head_speed,
                "acceleration": head_accel,
                "speed_limit": track_limit,
                "run_mode": ctrl.running_mode.value if ctrl.running_mode else 0,
                "run_dir": 0 if ctrl.direction > 0 else 1,
                "power_pull": int(ctrl.throttle * 100),
                "net_pressure": 750 if self.power_supply.can_traction() else 0,
                "curr_station": curr_station_id,
                "next_station": next_station_id,
                "end_station": curr_station_id,
                "power_state": (0 if self.power_supply.can_traction() else 1),
                "door_states": door_states,
                "has_power": self.power_supply.can_traction(),
            }

        # 信号屏数据源
        def _sig_source():
            return {
                "speed": head_speed,
                "acceleration": head_accel,
                "speed_limit": track_limit,
                "mode": 5,
                "run_dir": 0 if ctrl.direction > 0 else 1,
                "curr_station": curr_station_id,
                "next_station": next_station_id,
                "end_station": curr_station_id,
                "pull_switch": 1 if ctrl.throttle > 0 else 0,
                "pull_state": int(ctrl.throttle * 100),
                "brake_state": int(ctrl.brake_level * 100),
                "urgency_stop": 1 if runtime.emergency else 0,
                "event_id": 0,
                "sig_state": 0,
                "train_no": 1,
                "next_station_dist": next_station_dist,
            }

        self.network.set_cab_network_source(_net_source)
        self.network.set_cab_signal_source(_sig_source)

    @staticmethod
    def _build_door_states(ctrl) -> list:
        """构造 6 节车厢的门状态列表。

        简化实现：左门/右门统一状态。
        bit0=左1, bit1=右1, bit8=左2, bit9=右2, ...
        """
        left_open = 1 if ctrl.left_door_open else 0
        right_open = 1 if ctrl.right_door_open else 0
        states = []
        for i in range(6):
            ds = (left_open << (i * 2)) | (right_open << (i * 2 + 1))
            states.append(ds)
        return states

    def network_disconnect(self) -> dict:
        """断开所有网络通信模块"""
        if not self.network_started:
            return {"ok": True, "message": "网络已断开"}
        self.network.stop()
        self.network_started = False
        self.recorder.record("网络", "Web ATS 断开联调网络连接", severity="INFO")
        return {"ok": True, "message": "网络连接已断开"}

    def cab_connect(self) -> dict:
        """启动司机台显示屏（网络屏+信号屏）"""
        if self.cab_started:
            return {"ok": True, "message": "司机台显示已连接"}
        self._setup_cab_display_source()
        self.network.cab_display.start()
        self.cab_started = True
        self.recorder.record("司机台显示", "启动网络屏+信号屏发送", severity="INFO")
        return {"ok": True, "message": "司机台显示已启动"}

    def cab_disconnect(self) -> dict:
        """停止司机台显示屏"""
        if not self.cab_started:
            return {"ok": True, "message": "司机台显示已断开"}
        self.network.cab_display.stop()
        self.cab_started = False
        self.recorder.record("司机台显示", "停止网络屏+信号屏发送", severity="INFO")
        return {"ok": True, "message": "司机台显示已断开"}

    def _setup_cab_display_source(self):
        """设置司机台显示屏数据源"""
        def _cab_display_source():
            return dict(self.cab_display_state)
        self.network.set_cab_network_source(_cab_display_source)
        self.network.set_cab_signal_source(_cab_display_source)

    def set_plc_output(self, updates: dict) -> dict:
        """更新 PLC 输出状态并立即发送一帧"""
        for key in updates:
            if key in self.plc_output_state:
                self.plc_output_state[key] = bool(updates[key])
        # 如果网络已连接，立即发送
        if self.network_started:
            try:
                self.network.plc.send_output(**self.plc_output_state)
            except Exception as e:
                return {"ok": False, "message": f"发送失败: {e}"}
        return {"ok": True, "message": "PLC输出已更新", "state": dict(self.plc_output_state)}

    def set_cab_display(self, updates: dict) -> dict:
        """更新司机台显示数据并立即发送一帧"""
        for key in updates:
            if key in self.cab_display_state:
                self.cab_display_state[key] = updates[key]
        if self.network_started:
            # 强制发送会被 CabDisplayClient 的下个周期自然覆盖
            pass
        return {"ok": True, "message": "司机台显示数据已更新", "state": dict(self.cab_display_state)}

    def _update_cab_display_from_simulation(self):
        """从仿真列车自动更新司机台显示屏数据"""
        if not self.dispatch or not self.dispatch.trains:
            return
        # 取第一列运行的列车
        runtime = None
        for r in self.dispatch.trains.values():
            if r.status.name in ("RUNNING", "DWELLING", "WAITING", "HELD"):
                runtime = r
                break
        if runtime is None:
            runtime = next(iter(self.dispatch.trains.values()), None)
        if runtime is None:
            return

        ctrl = runtime.controller
        speed_ms = ctrl.head_speed
        self.cab_display_state["speed"] = round(speed_ms, 2)
        if ctrl.states:
            self.cab_display_state["acceleration"] = round(ctrl.states[0].acceleration, 3)

        # 限速
        try:
            limit = self.signal.get_effective_speed_limit_for_direction(
                runtime.head_abs, ctrl.direction, runtime.track_adapter
            )
            self.cab_display_state["speed_limit"] = round(limit, 1)
        except Exception:
            pass

        # 运行方向: 1=正向, -1=反向
        self.cab_display_state["run_dir"] = 1 if ctrl.direction == 1 else -1

        # 运行模式: 0=惰行, 1=牵引, 2=制动, 4=快速制动
        throttle = ctrl.throttle
        brake = ctrl.brake_level
        if brake >= 0.9:
            self.cab_display_state["run_mode"] = 4  # 快速制动
        elif brake > 0.05:
            self.cab_display_state["run_mode"] = 2  # 制动
        elif throttle > 0.05:
            self.cab_display_state["run_mode"] = 1  # 牵引
        else:
            self.cab_display_state["run_mode"] = 0  # 惰行

        # 驾驶模式
        if ctrl.running_mode.name == "AUTOMATIC":
            self.cab_display_state["mode"] = 5  # ATO模式
        else:
            self.cab_display_state["mode"] = 0  # 人工模式

        # 牵引/制动级位百分比
        self.cab_display_state["power_pull"] = int(max(throttle, brake) * 100)

        # 车站信息
        if runtime.service_plan and runtime.plan_index < len(runtime.service_plan.station_ids):
            self.cab_display_state["curr_station"] = runtime.service_plan.station_ids[runtime.plan_index]
        self.cab_display_state["next_station"] = runtime.target_station_id or 0
        if runtime.service_plan:
            self.cab_display_state["end_station"] = runtime.service_plan.station_ids[-1]

        # 门状态 (0=关, 1=开)
        door_state = 1 if ctrl.any_door_open else 0
        self.cab_display_state["door_states"] = [door_state] * 4

        # 制动状态
        self.cab_display_state["brake_state"] = 1 if brake > 0.05 else 0
        self.cab_display_state["urgency_stop"] = 1 if ctrl.interlock.emergency_brake_required else 0

        # 供电状态
        self.cab_display_state["has_power"] = self.power_supply.can_traction()
        self.cab_display_state["power_state"] = 1 if self.cab_display_state["has_power"] else 0

        # 记录到运行日志
        self.recorder.record(
            "司机台",
            f"cab_display speed={self.cab_display_state['speed']*3.6:.1f}km/h "
            f"mode={self.cab_display_state['run_mode']} dir={self.cab_display_state['run_dir']} "
            f"station={self.cab_display_state['curr_station']}→{self.cab_display_state['next_station']} "
            f"power={self.cab_display_state['power_state']} brake={self.cab_display_state['brake_state']} "
            f"urg={self.cab_display_state['urgency_stop']}",
            severity="DEBUG",
        )

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
                if self.network_started:
                    self._feed_cab_display()
                self._record_replay_frame()
                # 从仿真列车自动更新司机台显示屏数据
                if self.cab_started:
                    self._update_cab_display_from_simulation()
                if self._fast_forward:
                    fast_train = self.dispatch.trains.get(
                        self._fast_forward["train_id"])
                    reached = (fast_train is None
                               or fast_train.emergency
                               or fast_train.status == TrainStatus.DWELLING
                               or fast_train.target_station_id
                               != self._fast_forward["target_station_id"])
                    if reached:
                        self.speed_multiplier = self._fast_forward["previous_rate"]
                        self._fast_forward = None
                # 周期发送PLC输出（每步100ms，每步都发）
                if self.network_started:
                    self._plc_output_counter += 1
                    if self._plc_output_counter >= 1:
                        self._plc_output_counter = 0
                        self.network.plc.send_output(**self.plc_output_state)
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

    def control_train(self, train_id: str, action: str,
                      value: Optional[str] = None) -> dict:
        """执行 Web 端单车驾驶、模式和车门控制。"""
        runtime = self.dispatch.trains.get(train_id)
        if runtime is None:
            return {"ok": False, "message": f"列车不存在: {train_id}"}
        controller = runtime.controller

        if action == "mode":
            modes = {"manual": RunningMode.MANUAL,
                     "automatic": RunningMode.AUTOMATIC}
            mode = modes.get(str(value).lower())
            if mode is None:
                return {"ok": False, "message": f"未知驾驶模式: {value}"}
            controller.set_running_mode(mode)
            runtime.status = (TrainStatus.MANUAL if mode == RunningMode.MANUAL
                              else TrainStatus.RUNNING)
            return {"ok": True, "message": f"{train_id} 已切换为{('手动' if mode == RunningMode.MANUAL else '自动')}驾驶"}

        if action == "level":
            levels = {
                "P1": ControlLevel.LOW_TRACTION,
                "P2": ControlLevel.MEDIUM_TRACTION,
                "P3": ControlLevel.FULL_TRACTION,
                "COAST": ControlLevel.COAST,
                "B1": ControlLevel.SERVICE_BRAKE,
                "B2": ControlLevel.FULL_BRAKE,
                "EB": ControlLevel.EMERGENCY_BRAKE,
            }
            level = levels.get(str(value).upper())
            if level is None:
                return {"ok": False, "message": f"未知司控器级位: {value}"}
            if controller.running_mode != RunningMode.MANUAL:
                return {"ok": False, "message": "请先切换为手动驾驶模式"}
            controller.apply_control_level(level)
            if level == ControlLevel.EMERGENCY_BRAKE:
                runtime.emergency = True
                runtime.status = TrainStatus.EMERGENCY_STOP
            return {"ok": True, "message": f"{train_id} 司控器已切换至 {str(value).upper()}"}

        if action == "door":
            side = str(value).lower()
            if side == "close":
                controller.close_door()
                return {"ok": True, "message": f"{train_id} 车门已关闭"}
            door = {"left": DoorSide.LEFT, "right": DoorSide.RIGHT}.get(side)
            if door is None:
                return {"ok": False, "message": f"未知车门命令: {value}"}
            if not controller.open_door(door):
                return {"ok": False, "message": "列车未完全停车，禁止开门"}
            return {"ok": True, "message": f"{train_id} {('左' if side == 'left' else '右')}侧车门已打开"}

        if action == "load":
            level = LoadLevel.__members__.get(str(value).upper())
            if level is None:
                return {"ok": False, "message": f"未知载荷等级: {value}"}
            controller.set_load_level(level)
            return {"ok": True, "message": f"{train_id} 载荷已设置为 {level.name}"}

        if action == "dwell":
            try:
                seconds = max(5.0, min(120.0, float(value)))
            except (TypeError, ValueError):
                return {"ok": False, "message": "停站时间必须为 5–120 秒"}
            runtime.auto_drive.dwell_time = seconds
            return {"ok": True, "message": f"{train_id} 停站时间已设置为 {seconds:g} 秒"}

        if action == "consist":
            consists = {"4M2T": CONSIST_4M2T, "6M0T": CONSIST_6M0T,
                        "1M4T": CONSIST_1M4T}
            consist = consists.get(str(value).upper())
            if consist is None:
                return {"ok": False, "message": f"未知编组预设: {value}"}
            if not controller.is_stopped:
                return {"ok": False, "message": "列车运行中，禁止更换编组"}
            controller.replace_consist(consist, runtime.track_adapter)
            return {"ok": True, "message": f"{train_id} 编组已切换为 {str(value).upper()}"}

        if action == "jump":
            try:
                station_id = int(value)
                position = self.dispatch.trains.get_station_track_position(
                    station_id, controller.direction)
            except (TypeError, ValueError, KeyError) as exc:
                return {"ok": False, "message": f"无法跳转至车站: {exc}"}
            controller.reset_states(position.segment_id, position.offset)
            runtime.status = TrainStatus.WAITING
            runtime.emergency = False
            runtime.blocked_reason = ""
            return {"ok": True, "message": f"{train_id} 已跳转至车站 {station_id}"}

        if action == "fast-forward":
            if runtime.target_station_id is None:
                return {"ok": False, "message": "当前列车尚未设置下一站"}
            self._fast_forward = {
                "train_id": train_id,
                "target_station_id": runtime.target_station_id,
                "previous_rate": self.speed_multiplier,
            }
            self.speed_multiplier = 10
            return {"ok": True, "message": f"{train_id} 已按 10× 快进，到站后自动恢复"}

        return {"ok": False, "message": f"未知驾驶操作: {action}"}

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
            # 根据运行方向选择查站策略：上行查后方（递减里程），下行查前方（递增里程）
            if controller.direction == 1:
                next_station = runtime.auto_drive.find_next_station(head_abs)
            else:
                next_station = runtime.auto_drive._find_station_reverse(head_abs)
            if next_station is None:
                return {"ok": False, "message": "切换自动失败: 无前方车站"}
            hint_seg = controller.states[0].position.segment_id if controller.states else None
            target = runtime.track_adapter.from_absolute(next_station.position,
                                                         hint_seg_id=hint_seg)
            runtime.auto_drive.set_target(target)
            controller.set_running_mode(RunningMode.AUTOMATIC)
            from src.dispatch.models import TrainStatus
            runtime.status = TrainStatus.MANUAL
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
        hint_seg = controller.states[0].position.segment_id if controller.states else None
        target = runtime.track_adapter.from_absolute(next_station.position,
                                                     hint_seg_id=hint_seg)
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
        ctrl = runtime.controller
        # 解析位置：用当前列车所在段的链方向作为消歧提示
        hint_seg = ctrl.states[0].position.segment_id if ctrl.states else None
        target = runtime.track_adapter.from_absolute(station.position - 50.0,
                                                     hint_seg_id=hint_seg)
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
                runtime.track_adapter.from_absolute(next_station.position,
                                                    hint_seg_id=target.segment_id)
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
            "evaluation": self._evaluate_run(),
            "simTime": round(self.dispatch.sim_time, 2),
        }

    def apply_scenario(self, scenario_id: str) -> dict:
        """应用可重复演示的故障场景，并记录到运行日志。"""
        labels = {
            "normal": "正常运行", "power_outage": "牵引供电中断",
            "low_voltage": "低电压运行", "occupancy_conflict": "区段占用冲突",
            "low_adhesion": "低黏着运行", "communication_outage": "通信中断",
        }
        label = labels.get(scenario_id)
        if label is None:
            return {"ok": False, "message": f"未知运行场景: {scenario_id}"}

        # 每次切换先清除上一个场景注入，确保演示可重复。
        self.power_supply.set_status(PowerStatus.NORMAL)
        self.dispatch.occupancy.clear_injected()
        self.network_fault_injected = False
        for train in self.dispatch.trains.values():
            train.controller.env.weather = WeatherType.DRY

        if scenario_id == "power_outage":
            self.power_supply.set_status(PowerStatus.POWER_OFF)
        elif scenario_id == "low_voltage":
            self.power_supply.set_status(PowerStatus.LOW_VOLTAGE)
        elif scenario_id == "low_adhesion":
            for train in self.dispatch.trains.values():
                train.controller.env.weather = WeatherType.RAIN
        elif scenario_id == "communication_outage":
            self.network_fault_injected = True
        elif scenario_id == "occupancy_conflict":
            train = next(iter(self.dispatch.trains.values()), None)
            if train is None:
                return {"ok": False, "message": "没有列车，无法注入占用冲突"}
            route = self.dispatch._route_window(train)
            segment_id = route[0] if route else train.controller.states[0].position.segment_id
            self.dispatch.occupancy.inject(segment_id, "场景占用")
        self.dispatch.occupancy.update(self.dispatch.trains.values())

        self.active_scenario = scenario_id
        self.recorder.record(
            "场景", f"应用场景：{label}", source="web-ats",
            entity_id=scenario_id,
        )
        return {"ok": True, "message": f"场景已切换为{label}"}

    def _record_replay_frame(self):
        """以 2 Hz 保存轻量快照，供 Web 时间轴回放。"""
        sim_time = self.dispatch.sim_time
        if sim_time - self._last_replay_sample < 0.5:
            return
        self._last_replay_sample = sim_time
        self.replay_frames.append({
            "simTime": round(sim_time, 2),
            "powerStatus": self.power_supply.status.name,
            "trains": [
                {
                    "id": train.train_id,
                    "speedKmh": round(train.speed_kmh, 2),
                    "position": round(train.head_abs, 2),
                    "status": train.status.value,
                    "emergency": train.emergency,
                }
                for train in self.dispatch.trains.values()
            ],
        })

    def replay_snapshot(self) -> dict:
        frames = list(self.replay_frames)
        evaluation = self._evaluate_run()
        return {
            "ok": True,
            "scenario": self.active_scenario,
            "sampleIntervalMs": 500,
            "frames": frames,
            "eventCount": len(self.recorder.events),
            "score": evaluation["score"],
            "grade": evaluation["grade"],
            "dimensions": evaluation["dimensions"],
            "evaluationBasis": evaluation["evaluationBasis"],
            "weights": evaluation["weights"],
        }

    def _evaluate_run(self) -> dict:
        """向统一评价器提供 Web 可采集的回放与能耗数据。"""
        regen_ratios = [
            runtime.controller.energy_regen_ratio
            for runtime in self.dispatch.trains.values()
            if runtime.controller.energy_traction_kwh > 0.0
        ]
        return self.evaluator.evaluate(
            self.recorder,
            frames=list(self.replay_frames),
            regen_ratios=regen_ratios,
        )

    def snapshot(self) -> dict:
        self._snapshot_sequence += 1
        occupancy = self.dispatch.occupancy.snapshot
        locks = self.dispatch.interlocking.locks
        trains = [self._serialize_train(runtime)
                  for runtime in self.dispatch.trains.values()]
        # ATS 布局保留同名信号机的不同物理位置，运行态按名称映射灯色。
        runtime_signals = {signal.signal_id: signal for signal in self.track.signals}
        signals = []
        for signal in self.ats_signals:
            runtime_signal = runtime_signals.get(signal.name)
            aspect = (self.signal_system.get_signal_aspect(runtime_signal).name
                      if runtime_signal is not None else "RED")
            position = self.link_mapper.to_link_position(
                TrackPosition(signal.seg_id, signal.offset_m))
            signals.append({
                "key": f"{signal.layout_id}:{signal.seg_id}:{signal.offset_m}",
                "layoutId": signal.layout_id,
                "id": signal.name,
                "segmentId": signal.seg_id,
                "offset": signal.offset_m,
                "direction": signal.direction,
                "aspect": aspect,
                "linkPosition": round(position, 3) if position is not None else None,
            })
        events = [
            {"id": index, **asdict(event)}
            for index, event in enumerate(self.recorder.events[-120:])
        ]
        active_alarms = [
            event for event in events
            if event["severity"] in {"WARNING", "CRITICAL"}
        ]
        network_status = self.network.connection_status
        if self.network_fault_injected:
            network_status = {key: False for key in network_status}
        first_train = next(iter(self.dispatch.trains.values()), None)
        weather = (first_train.controller.env.weather
                   if first_train is not None else WeatherType.DRY)
        return {
            "type": "snapshot",
            "sequence": self._snapshot_sequence,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "dataSources": {
                "simulation": "realtime",
                "track": "database",
                "power": "simulation",
                "network": "realtime",
                "replay": "recorded",
            },
            "simTime": round(self.dispatch.sim_time, 2),
            "paused": self.paused,
            "speedMultiplier": self.speed_multiplier,
            "activeScenario": self.active_scenario,
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
            "network": network_status,
            "network_stats": self.network.network_stats,
            "network_started": self.network_started,
            "cab_started": self.cab_started,
            "plc_output_state": dict(self.plc_output_state),
            "cab_display_state": dict(self.cab_display_state),
            "networkInterfaces": [
                {"id": "vehicle_udp",     "label": "车辆UDP",     "protocol": "UDP", "cycleMs": 20,  "bidirectional": True},
                {"id": "signal_gateway",  "label": "信号网关",    "protocol": "UDP", "cycleMs": 250, "bidirectional": True},
                {"id": "plc",             "label": "司机台PLC",   "protocol": "TCP", "cycleMs": 100, "bidirectional": True},
                {"id": "vision",          "label": "视景系统",    "protocol": "UDP", "cycleMs": 100, "bidirectional": False},
                {"id": "cab_display",     "label": "司机台显示",  "protocol": "TCP", "cycleMs": 100, "bidirectional": True},
            ],
            "plc_data": self.network.plc_data,
            "networkFaultInjected": self.network_fault_injected,
            "environment": {
                "weather": weather.value,
                "adhesionCoefficient": (first_train.controller.env.get_adhesion_coefficient()
                                        if first_train is not None else 0.18),
            },
            "networkInterfaces": [
                {"id": "signal_gateway", "label": "信号系统网关",
                 "protocol": "UDP", "cycleMs": 250, "bidirectional": True},
                {"id": "vehicle_udp", "label": "车辆状态接口",
                 "protocol": "UDP", "cycleMs": 20, "bidirectional": True},
                {"id": "plc", "label": "实体司机台 PLC",
                 "protocol": "TCP", "cycleMs": 100, "bidirectional": True},
                {"id": "vision", "label": "视景系统",
                 "protocol": "UDP", "cycleMs": 100, "bidirectional": False},
                {"id": "cab_display", "label": "司机台显示屏",
                 "protocol": "TCP", "cycleMs": 100, "bidirectional": False},
            ],
            "alarms": {
                "active": len(active_alarms),
                "critical": sum(event["severity"] == "CRITICAL"
                                for event in active_alarms),
                "warning": sum(event["severity"] == "WARNING"
                               for event in active_alarms),
            },
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
                "signalCount": len(self.ats_signals),
                "switchCount": len(self.ats_switches),
                "totalLength": round(self.track.total_length(), 0),
            },
            "events": events,
            "evaluation": self._evaluate_run(),
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
            "switchComponents": self._serialize_switch_components(),
        }

    def _serialize_switch_components(self) -> list[dict]:
        """把反位 Seg 连通分量整理为 Web 可直接绘制的渡线组件。"""
        main_ids = {
            segment.seg_id for segment in self.track.segments
            if self.link_mapper.link_for_segment(segment.seg_id)
        }
        adjacency = {segment.seg_id: set() for segment in self.track.segments}
        for segment in self.track.segments:
            for neighbor in (segment.start_neighbor, segment.start_lateral,
                             segment.end_neighbor, segment.end_lateral):
                if neighbor in adjacency and neighbor not in (0, 65535):
                    adjacency[segment.seg_id].add(neighbor)
                    adjacency[neighbor].add(segment.seg_id)

        candidates = [
            switch for switch in self.ats_switches
            if switch.merge_seg_id in main_ids
            and switch.normal_seg_id in main_ids
            and switch.reverse_seg_id not in main_ids
        ]
        grouped: dict[frozenset[int], list] = {}
        for switch in candidates:
            seen = {switch.reverse_seg_id}
            stack = [switch.reverse_seg_id]
            while stack:
                current = stack.pop()
                for neighbor in adjacency.get(current, ()):
                    if neighbor not in main_ids and neighbor not in seen:
                        seen.add(neighbor)
                        stack.append(neighbor)
            grouped.setdefault(frozenset(seen), []).append(switch)

        components = []
        for index, (segment_ids, switches) in enumerate(grouped.items(), start=1):
            points = []
            for switch in switches:
                reference = (switch.merge_seg_id
                             if self.link_mapper.link_for_segment(switch.merge_seg_id)
                             else switch.normal_seg_id)
                points.append({
                    "position": round(self._switch_anchor(
                        switch.merge_seg_id, switch.normal_seg_id,
                        switch.reverse_seg_id), 3),
                    "direction": self.link_mapper.direction_for_segment(reference),
                    "switchId": switch.switch_id,
                    "name": switch.name,
                    "mergeSegmentId": switch.merge_seg_id,
                    "normalSegmentId": switch.normal_seg_id,
                    "reverseSegmentId": switch.reverse_seg_id,
                })
            points.sort(key=lambda item: item["position"])
            direction_counts = {
                direction: sum(point["direction"] == direction for point in points)
                for direction in {point["direction"] for point in points}
            }
            if len(points) == 4 and direction_counts == {"down": 2, "up": 2}:
                role = "crossover"
            elif len(points) == 2 and set(direction_counts) == {"down", "up"}:
                role = "single-crossover"
            elif len(points) >= 2 and set(direction_counts) == {"down", "up"}:
                role = "turnback"
            elif len(points) == 1:
                role = "stub"
            else:
                role = "siding"
            components.append({
                "id": index,
                "role": role,
                "segmentIds": sorted(segment_ids),
                "points": points,
            })
        return sorted(components, key=lambda item: item["points"][0]["position"])

    def _switch_anchor(self, *segment_ids: int) -> float:
        """取道岔相邻正线 Link 端点中最接近的连接位置。"""
        links = [self.link_mapper.link_for_segment(segment_id)
                 for segment_id in segment_ids]
        links = [link for link in links if link is not None]
        if len(links) == 1:
            return (links[0].start_m + links[0].end_m) / 2.0
        candidates = []
        for left in links:
            for right in links:
                if left is right:
                    continue
                for left_pos in (left.start_m, left.end_m):
                    for right_pos in (right.start_m, right.end_m):
                        candidates.append((abs(left_pos - right_pos),
                                           (left_pos + right_pos) / 2.0))
        return min(candidates)[1]

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

        gradient = (runtime.track_adapter.get_gradient(head)
                    if head is not None else 0.0)
        car_forces = []
        if report:
            car_forces = [
                {
                    "carIndex": car.car_index + 1,
                    "speedKmh": round(car.velocity * 3.6, 2),
                    "tractiveForceKn": round(car.tractive_force / 1000, 2),
                    "brakeForceKn": round(abs(car.brake_force) / 1000, 2),
                    "electricBrakeKn": round(abs(car.electric_brake_force) / 1000, 2),
                    "frictionBrakeKn": round(abs(car.friction_brake_force) / 1000, 2),
                    "davisResistanceKn": round(abs(car.davis_resistance) / 1000, 2),
                    "gradeResistanceKn": round(car.grade_resistance / 1000, 2),
                    "frontCouplerKn": round(car.coupler_force_front / 1000, 2),
                    "rearCouplerKn": round(car.coupler_force_rear / 1000, 2),
                    "netForceKn": round(car.net_force / 1000, 2),
                    "adhesion": ("traction-limited" if car.traction_limited else
                                 "brake-limited" if car.brake_limited else "ok"),
                }
                for car in report.cars
            ]
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
            "gradientPermille": round(gradient, 3),
            "targetStationId": runtime.target_station_id,
            "targetStation": target.name if target else None,
            "targetDistance": (round(abs(target.position - head_abs), 1)
                               if target else None),
            "held": runtime.held,
            "emergency": runtime.emergency,
            "blockedReason": runtime.blocked_reason,
            "servicePlan": plan.name if plan else None,
            "runningMode": controller.running_mode.name,
            "loadLevel": controller.energy_calc.load_level.name,
            "dwellTime": round(runtime.auto_drive.dwell_time, 1),
            "consist": {
                "carCount": len(controller.consist),
                "motorCount": controller.consist.motor_count,
                "cars": [
                    {"name": config.name, "isMotor": config.is_motor,
                     "mass": config.mass, "length": config.length}
                    for config in (controller.consist[index]
                                   for index in range(len(controller.consist)))
                ],
            },
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
            "maxCouplerForceKn": (round(report.max_coupler_force / 1000, 2)
                                   if report else 0.0),
            "energy": {
                "tractionKwh": round(controller.energy_traction_kwh, 4),
                "regenKwh": round(controller.energy_regen_kwh, 4),
                "frictionLossKwh": round(controller.energy_friction_loss_kwh, 4),
                "auxKwh": round(controller.energy_aux_kwh, 4),
                "netKwh": round(controller.energy_net_kwh, 4),
                "regenRatio": round(controller.energy_regen_ratio, 4),
                "tractionPowerKw": round(controller.energy_last_step.traction_power_kw, 2) if controller.energy_last_step else 0.0,
                "regenPowerKw": round(controller.energy_last_step.regen_power_kw, 2) if controller.energy_last_step else 0.0,
                "auxPowerKw": round(controller.energy_last_step.aux_energy_j / controller.energy_last_step.dt / 1000, 2) if controller.energy_last_step and controller.energy_last_step.dt > 0 else 0.0,
            },
            "carForces": car_forces,
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
