"""多列车调度、交路停站与折返状态机。"""

from dataclasses import dataclass
from typing import Optional

from src.logger.recorder import Recorder
from src.track.data import TrackData
from src.vehicle.enums import DoorSide, RunningMode

from .interlocking import BlockOccupancyManager, InterlockingService
from .models import ServicePlan, TrainRuntime, TrainStatus
from .train_manager import TrainManager


@dataclass(frozen=True)
class DispatchResult:
    ok: bool
    message: str


class DispatchManager:
    """调度命令入口和所有列车的统一仿真时钟。"""

    ARRIVAL_TOLERANCE = 3.0

    def __init__(self, track: TrackData, recorder: Optional[Recorder] = None):
        self.track = track
        self.recorder = recorder
        self.trains = TrainManager(track)
        self.occupancy = BlockOccupancyManager()
        self.interlocking = InterlockingService(track, self.occupancy)
        self.service_plans: dict[str, ServicePlan] = {}
        self.sim_time = 0.0

    def add_service_plan(self, plan: ServicePlan):
        for station_id in plan.station_ids:
            self.trains.get_station(station_id)
        self.service_plans[plan.plan_id] = plan

    def default_service_plan(self) -> Optional[ServicePlan]:
        return next(iter(self.service_plans.values()), None)

    def add_train(self, train_id: str, start_station_id: Optional[int] = None,
                  direction: int = 1) -> DispatchResult:
        self.occupancy.update(self.trains.values())
        if start_station_id is not None:
            try:
                station = self.trains.get_station(start_station_id)
            except ValueError as exc:
                return DispatchResult(False, str(exc))
            segment_id = self.track.get_seg_id_at(station.position)
            owners = self.occupancy.owners(segment_id)
            if owners:
                return DispatchResult(
                    False,
                    f"{station.name} 所在区段已被 {', '.join(sorted(owners))} 占用",
                )
        try:
            runtime = self.trains.add_train(train_id, start_station_id, direction)
        except (ValueError, KeyError) as exc:
            return DispatchResult(False, str(exc))
        self._record("调度", f"加车 {train_id}，位置 {runtime.head_abs:.0f}m", runtime)
        self.occupancy.update(self.trains.values())
        return DispatchResult(True, f"已添加列车 {train_id}")

    def remove_train(self, train_id: str) -> DispatchResult:
        try:
            runtime = self.trains.require(train_id)
        except KeyError as exc:
            return DispatchResult(False, str(exc))
        if runtime.controller.head_speed > 0.1:
            return DispatchResult(False, "运行中的列车不能删除")
        self.interlocking.cancel_route(train_id)
        self.trains.remove_train(train_id)
        self.occupancy.update(self.trains.values())
        self._record("调度", f"删除列车 {train_id}", runtime)
        return DispatchResult(True, f"已删除列车 {train_id}")

    def assign_plan(self, train_id: str, plan_id: str) -> DispatchResult:
        runtime = self.trains.get(train_id)
        plan = self.service_plans.get(plan_id)
        if runtime is None:
            return DispatchResult(False, f"列车不存在: {train_id}")
        if plan is None:
            return DispatchResult(False, f"交路不存在: {plan_id}")

        runtime.service_plan = plan
        # 调度交路按站间运行，使用比单点演示更保守的制动窗口。
        runtime.auto_drive.stop_distance = max(runtime.auto_drive.stop_distance, 120.0)
        runtime.auto_drive.emergency_brake_distance = max(
            runtime.auto_drive.emergency_brake_distance, 2.0)
        runtime.auto_drive.approach_speed = min(runtime.auto_drive.approach_speed, 4.0)
        runtime.plan_index = self._nearest_plan_index(runtime, plan)
        runtime.plan_step = 1 if runtime.controller.direction > 0 else -1
        # 从终端站加入交路时自动完成一次换端，使首次发车方向有效。
        if plan.turnback and runtime.plan_index == len(plan.station_ids) - 1 \
                and runtime.plan_step > 0:
            runtime.controller.reverse_direction()
            runtime.plan_step = -1
        elif plan.turnback and runtime.plan_index == 0 and runtime.plan_step < 0:
            runtime.controller.reverse_direction()
            runtime.plan_step = 1
        runtime.target_station_id = None
        runtime.status = TrainStatus.WAITING
        runtime.blocked_reason = ""
        self._record("调度", f"{train_id} 设置交路：{plan.name}", runtime)
        return DispatchResult(True, f"{train_id} 已设置交路 {plan.name}")

    def depart(self, train_id: str) -> DispatchResult:
        runtime = self.trains.get(train_id)
        if runtime is None:
            return DispatchResult(False, f"列车不存在: {train_id}")
        if runtime.service_plan is None:
            return DispatchResult(False, "请先设置交路")
        runtime.held = False
        runtime.emergency = False
        runtime.controller.interlock.emergency_brake_required = False
        runtime.controller.interlock.traction_permitted = True
        if runtime.target_station_id is None:
            result = self._prepare_next_leg(runtime)
            if not result.ok:
                return result
        runtime.controller.close_door()
        runtime.status = TrainStatus.RUNNING
        self._record("调度", f"{train_id} 发车", runtime)
        return DispatchResult(True, f"{train_id} 已发车")

    def hold(self, train_id: str) -> DispatchResult:
        runtime = self.trains.get(train_id)
        if runtime is None:
            return DispatchResult(False, f"列车不存在: {train_id}")
        runtime.held = True
        runtime.status = TrainStatus.HELD
        runtime.controller.set_throttle(0.0)
        runtime.controller.set_brake(0.7)
        self._record("调度", f"{train_id} 执行扣车", runtime)
        return DispatchResult(True, f"{train_id} 已扣车")

    def release(self, train_id: str) -> DispatchResult:
        runtime = self.trains.get(train_id)
        if runtime is None:
            return DispatchResult(False, f"列车不存在: {train_id}")
        runtime.held = False
        runtime.controller.set_brake(0.0)
        runtime.status = TrainStatus.WAITING
        return self.depart(train_id)

    def emergency_stop(self, train_id: str) -> DispatchResult:
        runtime = self.trains.get(train_id)
        if runtime is None:
            return DispatchResult(False, f"列车不存在: {train_id}")
        runtime.emergency = True
        runtime.status = TrainStatus.EMERGENCY_STOP
        runtime.controller.interlock.emergency_brake_required = True
        runtime.controller.emergency_brake()
        self._record("安全", f"{train_id} 调度紧急停车", runtime)
        return DispatchResult(True, f"{train_id} 已紧急停车")

    def restore(self, train_id: str) -> DispatchResult:
        runtime = self.trains.get(train_id)
        if runtime is None:
            return DispatchResult(False, f"列车不存在: {train_id}")
        runtime.emergency = False
        runtime.controller.interlock.emergency_brake_required = False
        runtime.controller.set_brake(0.0)
        runtime.status = TrainStatus.WAITING
        self._record("调度", f"{train_id} 解除紧急停车", runtime)
        return DispatchResult(True, f"{train_id} 已恢复，等待发车")

    def step(self, dt: float) -> dict[str, object]:
        """统一推进全部列车，返回各列车本步力学报告。"""
        reports: dict[str, object] = {}
        runtimes = tuple(self.trains.values())
        self.occupancy.update(runtimes)

        for runtime in runtimes:
            self._apply_dispatch_state(runtime, dt)
            reports[runtime.train_id] = runtime.controller.step(dt)
            self._check_arrival(runtime)

        self.occupancy.update(runtimes)
        self.sim_time += dt
        return reports

    def _apply_dispatch_state(self, runtime: TrainRuntime, dt: float):
        controller = runtime.controller
        if runtime.emergency:
            controller.interlock.emergency_brake_required = True
            controller.emergency_brake()
            return
        controller.interlock.emergency_brake_required = False

        if runtime.held:
            controller.interlock.traction_permitted = False
            controller.set_throttle(0.0)
            controller.set_brake(0.7)
            return

        if runtime.status in (TrainStatus.DWELLING, TrainStatus.TURNING_BACK):
            controller.interlock.traction_permitted = False
            controller.set_throttle(0.0)
            controller.set_brake(0.7 if not controller.is_stopped else 0.0)
            if controller.is_stopped:
                runtime.dwell_remaining = max(0.0, runtime.dwell_remaining - dt)
                if runtime.dwell_remaining <= 0.0:
                    self._finish_dwell(runtime)
            return

        if runtime.status not in (TrainStatus.RUNNING, TrainStatus.BLOCKED):
            if runtime.status == TrainStatus.MANUAL:
                controller.interlock.traction_permitted = True
                if controller.running_mode == RunningMode.AUTOMATIC:
                    runtime.auto_drive.step()
                return
            controller.interlock.traction_permitted = False
            controller.coast()
            return

        route_result = self.interlocking.request_route(
            runtime.train_id, self._route_window(runtime))
        if not route_result.granted:
            runtime.status = TrainStatus.BLOCKED
            runtime.blocked_reason = route_result.reason
            controller.interlock.traction_permitted = False
            controller.set_throttle(0.0)
            controller.set_brake(0.7)
            return

        runtime.blocked_reason = ""
        runtime.status = TrainStatus.RUNNING
        controller.interlock.traction_permitted = True
        runtime.auto_drive.step()

    def _check_arrival(self, runtime: TrainRuntime):
        if runtime.status != TrainStatus.RUNNING:
            return
        distance = runtime.auto_drive.distance_to_target
        if distance is None:
            return
        if abs(distance) <= self.ARRIVAL_TOLERANCE and runtime.controller.is_stopped:
            self.interlocking.cancel_route(runtime.train_id)
            plan = runtime.service_plan
            if plan is None:
                return
            station = self.trains.get_station(runtime.target_station_id)
            runtime.plan_index = plan.station_ids.index(station.station_id)
            runtime.target_station_id = None
            runtime.dwell_remaining = plan.dwell_time
            terminal = runtime.plan_index in (0, len(plan.station_ids) - 1)
            runtime.status = (TrainStatus.TURNING_BACK
                              if terminal and plan.turnback else TrainStatus.DWELLING)
            side = self.track.get_platform_side_at(runtime.head_abs)
            if side == "left":
                runtime.controller.open_door(DoorSide.LEFT)
            elif side == "right":
                runtime.controller.open_door(DoorSide.RIGHT)
            self._record("到站", f"{runtime.train_id} 到达 {station.name}", runtime)

    def _finish_dwell(self, runtime: TrainRuntime):
        plan = runtime.service_plan
        if plan is None:
            runtime.status = TrainStatus.COMPLETED
            return
        runtime.controller.close_door()
        at_terminal = runtime.plan_index in (0, len(plan.station_ids) - 1)
        if at_terminal:
            if not plan.turnback:
                runtime.status = TrainStatus.COMPLETED
                self._record("调度", f"{runtime.train_id} 交路完成", runtime)
                return
            if runtime.controller.reverse_direction():
                runtime.plan_step *= -1
                runtime.completed_cycles += 1
                self._record("折返", f"{runtime.train_id} 完成换端", runtime)

        result = self._prepare_next_leg(runtime)
        if not result.ok:
            runtime.status = TrainStatus.BLOCKED
            runtime.blocked_reason = result.message
            return
        runtime.status = TrainStatus.RUNNING

    def _prepare_next_leg(self, runtime: TrainRuntime) -> DispatchResult:
        plan = runtime.service_plan
        if plan is None:
            return DispatchResult(False, "未设置交路")
        next_index = runtime.plan_index + runtime.plan_step
        if not 0 <= next_index < len(plan.station_ids):
            return DispatchResult(False, "交路已到终点")
        target_station = self.trains.get_station(plan.station_ids[next_index])
        target_position = runtime.track_adapter.from_absolute(target_station.position)
        runtime.auto_drive.set_target(target_position)
        runtime.controller.set_running_mode(RunningMode.AUTOMATIC)
        runtime.target_station_id = target_station.station_id
        runtime.reserved_segments = self.interlocking.route_between(
            runtime.head_abs, target_station.position, runtime.controller.direction)
        request = self.interlocking.request_route(
            runtime.train_id, self._route_window(runtime))
        if not request.granted:
            runtime.blocked_reason = request.reason
            return DispatchResult(False, request.reason)
        runtime.blocked_reason = ""
        return DispatchResult(True, f"目标站 {target_station.name}")

    @staticmethod
    def _route_window(runtime: TrainRuntime) -> tuple[int, ...]:
        """只锁闭当前及前方一个区段，形成可连续释放的滚动进路。"""
        route = runtime.reserved_segments
        if not route:
            return ()
        head_segment = runtime.controller.states[0].position.segment_id
        try:
            index = route.index(head_segment)
        except ValueError:
            index = 0
        return route[index:index + 2]

    def _nearest_plan_index(self, runtime: TrainRuntime,
                            plan: ServicePlan) -> int:
        return min(
            range(len(plan.station_ids)),
            key=lambda index: abs(
                self.trains.get_station(plan.station_ids[index]).position
                - runtime.head_abs
            ),
        )

    def _record(self, event_type: str, description: str,
                runtime: Optional[TrainRuntime] = None):
        if self.recorder is None:
            return
        position = runtime.head_abs if runtime is not None else 0.0
        speed = runtime.controller.head_speed if runtime is not None else 0.0
        self.recorder.record(event_type, description, position, speed)
