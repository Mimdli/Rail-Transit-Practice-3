"""Web 仿真运行时：统一管理状态、命令和仿真时钟。"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import asdict
from typing import Callable, Optional

from src.dispatch import DispatchManager, DispatchResult, ServicePlan
from src.logger.recorder import Recorder
from src.network.manager import NetworkManager
from src.power.supply import PowerStatus, PowerSupply
from src.signal.system import SignalSystem
from src.track.db_loader import DBLoader
from src.track.link_mainline import LinkCoordinateMapper, load_mainline_links
from src.track.semantic_line import build_semantic_line


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
        self._task: Optional[asyncio.Task] = None
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
            "network": self.network.connection_status,
            "network_stats": self.network.network_stats,
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
