"""Web 仿真运行时：统一管理状态、命令和仿真时钟。"""

from __future__ import annotations

import asyncio
from collections import deque
from contextlib import suppress
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Callable, Optional

from src.dispatch import DispatchManager, DispatchResult, ServicePlan
from src.logger.recorder import Recorder
from src.network.manager import NetworkManager
from src.power.supply import PowerStatus, PowerSupply
from src.signal.system import SignalSystem
from src.track.db_loader import DBLoader
from src.track.link_mainline import LinkCoordinateMapper, load_mainline_links
from src.common.track_position import TrackPosition
from src.track.semantic_line import build_semantic_line
from src.vehicle.environment import WeatherType


class SimulationRuntime:
    """保证所有领域对象只在同一个 asyncio 事件循环中访问。"""

    STEP_SECONDS = 0.1

    def __init__(self):
        self.track = DBLoader().load_from_db()
        self.recorder = Recorder()
        self.recorder.start()
        self.signal_system = SignalSystem()
        self.power_supply = PowerSupply()
        self.network = NetworkManager()
        self.semantic_line = build_semantic_line(self.track)
        self.link_mapper = LinkCoordinateMapper(self.track)
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
        self.network_started = False
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
        self.dispatch.add_train("1车", station_ids[0], direction=1)
        self.dispatch.assign_plan("1车", "mainline_loop")

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

        # 4. PLC 接收回调
        def _on_plc_recv(data: dict):
            self.recorder.record("PLC", str(data), severity="INFO")

        self.network.set_plc_recv_callback(_on_plc_recv)

        # 5. 视景系统数据源：使用默认内部数据生成
        #    VisionUDPClient 内部已有 TCMS2VIEW 数据生成逻辑，无需手动设置

        # 6. 司机台显示屏数据源：使用默认内部数据生成
        #    CabDisplayClient 内部已有网络屏/信号屏数据生成逻辑，无需手动设置

    def network_disconnect(self) -> dict:
        """断开所有网络通信模块"""
        if not self.network_started:
            return {"ok": True, "message": "网络已断开"}
        self.network.stop()
        self.network_started = False
        self.recorder.record("网络", "Web ATS 断开联调网络连接", severity="INFO")
        return {"ok": True, "message": "网络连接已断开"}

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

    async def _run(self):
        while True:
            if not self.paused:
                dt = self.STEP_SECONDS * self.speed_multiplier
                if not self.power_supply.can_traction():
                    for runtime in self.dispatch.trains.values():
                        runtime.controller.set_throttle(0.0)
                self.dispatch.step(dt)
                self.power_supply.step(dt)
                self.recorder.step(dt)
                self._record_replay_frame()
                # 周期发送PLC输出（每步100ms，每步都发）
                if self.network_started:
                    self._plc_output_counter += 1
                    if self._plc_output_counter >= 1:
                        self._plc_output_counter = 0
                        self.network.plc.send_output(**self.plc_output_state)
            await asyncio.sleep(self.STEP_SECONDS)

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
        emergency_count = sum(
            1 for event in self.recorder.events
            if event.severity == "CRITICAL"
        )
        return {
            "ok": True,
            "scenario": self.active_scenario,
            "sampleIntervalMs": 500,
            "frames": frames,
            "eventCount": len(self.recorder.events),
            "score": max(0, 100 - emergency_count * 10),
        }

    def snapshot(self) -> dict:
        self._snapshot_sequence += 1
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
                "linkPosition": (
                    round(pos, 3) if (pos := self.link_mapper.to_link_position(
                        TrackPosition(signal.seg_id, signal.offset)
                    )) is not None else None
                ),
            }
            for signal in self.track.signals
        ]
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
            "network": network_status,
            "network_stats": self.network.network_stats,
            "network_started": self.network_started,
            "plc_output_state": dict(self.plc_output_state),
            "networkInterfaces": [
                {"id": "vehicle_udp",     "label": "车辆UDP",     "protocol": "UDP", "cycleMs": 20,  "bidirectional": True},
                {"id": "signal_gateway",  "label": "信号网关",    "protocol": "UDP", "cycleMs": 250, "bidirectional": True},
                {"id": "plc",             "label": "司机台PLC",   "protocol": "TCP", "cycleMs": 100, "bidirectional": True},
                {"id": "vision",          "label": "视景系统",    "protocol": "UDP", "cycleMs": 100, "bidirectional": False},
                {"id": "cab_display",     "label": "司机台显示",  "protocol": "TCP", "cycleMs": 100, "bidirectional": False},
            ],
            "networkFaultInjected": self.network_fault_injected,
            "environment": {
                "weather": weather.value,
                "adhesionCoefficient": (first_train.controller.env.get_adhesion_coefficient()
                                        if first_train is not None else 0.18),
            },
            "networkInterfaces": [
                {"id": "signal_gateway", "label": "信号系统网关",
                 "protocol": "UDP", "cycleMs": 250},
                {"id": "vehicle_udp", "label": "车辆状态接口",
                 "protocol": "UDP", "cycleMs": 20},
                {"id": "plc", "label": "实体司机台 PLC",
                 "protocol": "TCP", "cycleMs": 100},
                {"id": "vision", "label": "视景系统",
                 "protocol": "UDP", "cycleMs": 100},
                {"id": "cab_display", "label": "司机台显示屏",
                 "protocol": "TCP", "cycleMs": 100},
            ],
            "alarms": {
                "active": len(active_alarms),
                "critical": sum(event["severity"] == "CRITICAL"
                                for event in active_alarms),
                "warning": sum(event["severity"] == "WARNING"
                               for event in active_alarms),
            },
            "stations": [
                {"id": item.station_id, "name": item.name,
                 "position": item.position}
                for item in sorted(self.track.stations,
                                   key=lambda station: station.position)
            ],
            "line": self._serialize_line(),
            "events": events,
        }

    def _serialize_line(self) -> dict:
        """输出与桌面运营线路图一致的 Link 主线和语义分支。"""
        links = load_mainline_links()
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
        return {
            "id": runtime.train_id,
            "status": runtime.status.value,
            "direction": runtime.direction_label,
            "directionCode": controller.direction,
            "speedKmh": round(runtime.speed_kmh, 2),
            "headPosition": round(runtime.head_abs, 3),
            "linkPosition": (round(link_position, 3)
                             if link_position is not None else None),
            "segmentId": head.segment_id if head else None,
            "offset": round(head.offset, 3) if head else None,
            "speedLimitKmh": round(limit, 1),
            "targetStationId": runtime.target_station_id,
            "targetStation": target.name if target else None,
            "targetDistance": (round(abs(target.position - runtime.head_abs), 1)
                               if target else None),
            "held": runtime.held,
            "emergency": runtime.emergency,
            "blockedReason": runtime.blocked_reason,
            "servicePlan": plan.name if plan else None,
            "throttle": round(controller.throttle, 3),
            "brakeLevel": round(controller.brake_level, 3),
            "acceleration": (round(controller.states[0].acceleration, 3)
                             if controller.states else 0.0),
            "tractiveForceKn": (round(report.total_tractive_force / 1000, 1)
                                if report else 0.0),
            "brakeForceKn": (round(sum(abs(car.brake_force)
                                        for car in report.cars) / 1000, 1)
                             if report else 0.0),
            "energyKwh": round(controller.energy_net_kwh, 3),
            "doors": {
                "left": controller.left_door_open,
                "right": controller.right_door_open,
            },
        }
