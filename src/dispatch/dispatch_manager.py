"""多列车调度、交路停站与折返状态机。"""

import heapq
from dataclasses import dataclass
from typing import Optional

from src.logger.recorder import Recorder
from src.track.data import TrackData
from src.vehicle.enums import DoorSide, RunningMode
from src.signal.system import SignalAspect
from src.track.semantic_line import compute_station_route

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
    SEPARATION_DECELERATION = 1.0
    MINIMUM_SEPARATION = 30.0
    WARNING_MARGIN = 80.0
    MAX_SEPARATION_LOOKAHEAD = 2000.0

    def __init__(self, track: TrackData, recorder: Optional[Recorder] = None,
                 signal_system=None):
        self.track = track
        self.recorder = recorder
        self.trains = TrainManager(track)
        self.occupancy = BlockOccupancyManager()
        self.interlocking = InterlockingService(track, self.occupancy)
        self.signal_system = signal_system
        self.service_plans: dict[str, ServicePlan] = {}
        self.sim_time = 0.0
        self._separation_levels: dict[tuple[str, str], str] = {}
        self._collision_pairs: set[frozenset[str]] = set()

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
            segment_id = self.trains.get_station_track_position(
                start_station_id, direction).segment_id
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
        self._separation_levels = {
            pair: level for pair, level in self._separation_levels.items()
            if train_id not in pair
        }
        self._collision_pairs = {
            pair for pair in self._collision_pairs if train_id not in pair
        }
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
        self._record("发车", f"{train_id} 发车", runtime)
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
        self._record("紧急制动", f"{train_id} 调度紧急停车", runtime,
                     severity="CRITICAL")
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
        self._refresh_signals()
        self._detect_train_collisions(runtimes)

        for runtime in runtimes:
            self._apply_dispatch_state(runtime, dt)
            reports[runtime.train_id] = runtime.controller.step(dt)
            self._check_arrival(runtime)

        self.occupancy.update(runtimes)
        self._refresh_signals()
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

        if self._apply_train_separation_protection(runtime):
            return

        if runtime.status not in (TrainStatus.RUNNING, TrainStatus.BLOCKED):
            if runtime.status == TrainStatus.MANUAL:
                controller.interlock.traction_permitted = True
                if self._apply_signal_protection(runtime):
                    return
                if controller.running_mode == RunningMode.AUTOMATIC:
                    runtime.auto_drive.step()
                    self._apply_yellow_speed_control(runtime)
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

        self._refresh_signals()
        if self._apply_signal_protection(runtime):
            return
        runtime.blocked_reason = ""
        runtime.status = TrainStatus.RUNNING
        controller.interlock.traction_permitted = True
        runtime.auto_drive.step()
        self._apply_yellow_speed_control(runtime)

    def _refresh_signals(self):
        if self.signal_system is None:
            return
        self.signal_system.update_from_dispatch(
            self.track.signals, self.track,
            self.occupancy.snapshot, self.interlocking.locks)

    def _apply_signal_protection(self, runtime: TrainRuntime) -> bool:
        """红灯前切除牵引并制动，形成调度—信号—车辆闭环。"""
        if self.signal_system is None:
            return False
        controller = runtime.controller
        braking_distance = max(
            self.signal_system.approach_range,
            controller.head_speed ** 2 / 1.4 + 40.0,
        )
        signal = self.signal_system.get_nearest_signal_for_direction(
            runtime.head_abs, controller.direction, self.track.signals,
            look_ahead=braking_distance,
            allowed_segment_ids=self._signal_route_segments(runtime))
        if signal is None:
            self._clear_signal_block(runtime)
            return False
        if self.signal_system.get_signal_aspect(signal) != SignalAspect.RED:
            self._clear_signal_block(runtime)
            return False
        distance = controller.direction * (signal.position - runtime.head_abs)
        runtime.blocked_reason = f"前方信号 {signal.signal_id} 红灯（{distance:.0f}m）"
        runtime.status = TrainStatus.BLOCKED
        controller.interlock.traction_permitted = False
        controller.set_throttle(0.0)
        controller.set_brake(0.7)
        return True

    @staticmethod
    def _clear_signal_block(runtime: TrainRuntime):
        if not runtime.blocked_reason.startswith("前方信号"):
            return
        runtime.blocked_reason = ""
        runtime.status = (TrainStatus.RUNNING
                          if runtime.target_station_id is not None
                          else TrainStatus.MANUAL)

    def _apply_yellow_speed_control(self, runtime: TrainRuntime):
        if self.signal_system is None:
            return
        signal = self.signal_system.get_nearest_signal_for_direction(
            runtime.head_abs, runtime.controller.direction,
            self.track.signals, look_ahead=500.0,
            allowed_segment_ids=self._signal_route_segments(runtime))
        if (signal is not None
                and self.signal_system.get_signal_aspect(signal) == SignalAspect.YELLOW
                and runtime.controller.head_speed > self.signal_system.yellow_speed_limit):
            runtime.controller.set_throttle(0.0)
            runtime.controller.set_brake(0.4)

    def _apply_train_separation_protection(self,
                                           runtime: TrainRuntime) -> bool:
        """直接根据前车距离制动，不依赖调度进路或信号灯色。"""
        if runtime.status not in (
                TrainStatus.MANUAL, TrainStatus.RUNNING, TrainStatus.BLOCKED):
            return False
        nearest = self._nearest_train_ahead(runtime)
        if nearest is None:
            self._clear_separation_state(runtime)
            return False

        other, clearance, closing_speed = nearest
        braking_distance = (
            closing_speed ** 2 / (2.0 * self.SEPARATION_DECELERATION)
            + self.MINIMUM_SEPARATION
        )
        key = (runtime.train_id, other.train_id)
        if clearance <= braking_distance:
            if self._separation_levels.get(key) != "brake":
                self._record(
                    "安全防护",
                    f"{runtime.train_id} 与前车 {other.train_id} "
                    f"净间距 {max(0.0, clearance):.1f}m，强制制动",
                    runtime, severity="WARNING", entity_id=other.train_id,
                    source="safety",
                )
            self._separation_levels[key] = "brake"
            self._clear_other_separation_pairs(runtime.train_id, key)
            runtime.status = TrainStatus.BLOCKED
            runtime.blocked_reason = (
                f"前车 {other.train_id} 安全距离不足"
                f"（{max(0.0, clearance):.0f}m）")
            runtime.controller.interlock.traction_permitted = False
            runtime.controller.set_throttle(0.0)
            runtime.controller.set_brake(0.85)
            return True

        warning_distance = braking_distance + self.WARNING_MARGIN
        if clearance <= warning_distance:
            if key not in self._separation_levels:
                self._record(
                    "列车接近",
                    f"{runtime.train_id} 接近 {other.train_id}，"
                    f"净间距 {clearance:.1f}m",
                    runtime, severity="WARNING", entity_id=other.train_id,
                    source="safety",
                )
            self._separation_levels[key] = "warning"
            self._clear_other_separation_pairs(runtime.train_id, key)
        else:
            self._clear_separation_state(runtime)
        return False

    def _detect_train_collisions(self,
                                 runtimes: tuple[TrainRuntime, ...]):
        """车体重叠时对双方施加紧急制动并仅记录一次。"""
        for runtime in runtimes:
            nearest = self._nearest_train_ahead(runtime)
            if nearest is None:
                continue
            other, clearance, _ = nearest
            if clearance > 0.0:
                continue
            pair = frozenset((runtime.train_id, other.train_id))
            for involved in (runtime, other):
                involved.emergency = True
                involved.status = TrainStatus.EMERGENCY_STOP
                involved.blocked_reason = (
                    f"与列车 {other.train_id if involved is runtime else runtime.train_id} "
                    "发生车体重叠")
                involved.controller.interlock.emergency_brake_required = True
                involved.controller.emergency_brake()
            if pair not in self._collision_pairs:
                self._record(
                    "列车碰撞",
                    f"{runtime.train_id} 与 {other.train_id} 车体重叠 "
                    f"{abs(clearance):.1f}m",
                    runtime, severity="CRITICAL", entity_id=other.train_id,
                    source="safety",
                )
                self._collision_pairs.add(pair)

    def _nearest_train_ahead(self, runtime: TrainRuntime):
        """返回同一拓扑路径上的最近列车及车体净间距。"""
        if not runtime.controller.states:
            return None
        start = runtime.controller.states[0].position
        best = None
        for other in self.trains.values():
            if other is runtime or not other.controller.states:
                continue
            distance = self._distance_ahead(
                start, other.controller.states[0].position,
                runtime.controller.direction,
                self.MAX_SEPARATION_LOOKAHEAD,
            )
            if distance is None:
                continue
            same_direction = (
                other.controller.direction == runtime.controller.direction)
            clearance = (distance - other.controller.consist.total_length
                         if same_direction else distance)
            own_speed = max(
                0.0, runtime.controller.direction
                * runtime.controller.head_speed)
            other_speed = max(
                0.0, other.controller.direction
                * other.controller.head_speed)
            closing_speed = (
                max(0.0, own_speed - other_speed)
                if same_direction else own_speed + other_speed
            )
            candidate = (other, clearance, closing_speed)
            if best is None or clearance < best[1]:
                best = candidate
        return best

    def _distance_ahead(self, start, target, direction: int,
                        max_distance: float) -> Optional[float]:
        """沿 Seg 邻接关系计算前向距离，避免并行线绝对里程相同造成误判。"""
        start_seg = self.track._seg_map.get(start.segment_id)
        if start_seg is None or target.segment_id not in self.track._seg_map:
            return None
        sign = 1 if direction >= 0 else -1
        if start.segment_id == target.segment_id:
            direct = sign * (target.offset - start.offset)
            return direct if 0.0 <= direct <= max_distance else None

        initial = (start_seg.length - start.offset
                   if sign > 0 else start.offset)
        heap = []
        for neighbor in self._forward_neighbors(start_seg, sign):
            heapq.heappush(heap, (max(0.0, initial), neighbor))
        visited: dict[int, float] = {}
        while heap:
            distance, segment_id = heapq.heappop(heap)
            if distance > max_distance:
                break
            if distance >= visited.get(segment_id, float("inf")):
                continue
            visited[segment_id] = distance
            segment = self.track._seg_map.get(segment_id)
            if segment is None:
                continue
            if segment_id == target.segment_id:
                inside = (target.offset if sign > 0
                          else segment.length - target.offset)
                total = distance + max(0.0, inside)
                return total if total <= max_distance else None
            next_distance = distance + segment.length
            for neighbor in self._forward_neighbors(segment, sign):
                heapq.heappush(heap, (next_distance, neighbor))
        return None

    def _forward_neighbors(self, segment, direction: int) -> tuple[int, ...]:
        raw = ((segment.end_neighbor, segment.end_lateral)
               if direction > 0 else
               (segment.start_neighbor, segment.start_lateral))
        return tuple(
            segment_id for segment_id in raw
            if segment_id in self.track._seg_map and segment_id not in (0, 65535)
        )

    def _clear_other_separation_pairs(self, train_id: str,
                                      keep: tuple[str, str]):
        self._separation_levels = {
            pair: level for pair, level in self._separation_levels.items()
            if pair[0] != train_id or pair == keep
        }

    def _clear_separation_state(self, runtime: TrainRuntime):
        self._clear_other_separation_pairs(runtime.train_id, ("", ""))
        if runtime.blocked_reason.startswith("前车"):
            runtime.blocked_reason = ""
            runtime.status = (TrainStatus.RUNNING
                              if runtime.target_station_id is not None
                              else TrainStatus.MANUAL)

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
            self._record("到站", f"{runtime.train_id} 到达 {station.name}", runtime,
                         entity_id=str(station.station_id))

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
        start_segment = runtime.controller.states[0].position.segment_id
        runtime.reserved_segments = compute_station_route(
            self.track, start_segment, target_station.station_id,
            runtime.controller.direction)
        if not runtime.reserved_segments:
            runtime.reserved_segments = self.interlocking.route_between(
                runtime.head_abs, target_station.position,
                runtime.controller.direction)
        request = self.interlocking.request_route(
            runtime.train_id, self._route_window(runtime))
        if not request.granted:
            runtime.blocked_reason = request.reason
            return DispatchResult(False, request.reason)
        runtime.blocked_reason = ""
        return DispatchResult(True, f"目标站 {target_station.name}")

    def _signal_route_segments(self, runtime: TrainRuntime) -> set[int]:
        if runtime.reserved_segments:
            return set(runtime.reserved_segments)
        stations = sorted(self.track.stations, key=lambda item: item.position)
        ahead = [station for station in stations
                 if runtime.controller.direction
                 * (station.position - runtime.head_abs) > 5.0]
        if not ahead:
            return {runtime.controller.states[0].position.segment_id}
        target = min(
            ahead,
            key=lambda station: runtime.controller.direction
            * (station.position - runtime.head_abs))
        route = compute_station_route(
            self.track, runtime.controller.states[0].position.segment_id,
            target.station_id, runtime.controller.direction)
        return set(route)

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
                runtime: Optional[TrainRuntime] = None, *,
                severity: Optional[str] = None, entity_id: str = "",
                source: str = "dispatch"):
        if self.recorder is None:
            return
        position = runtime.head_abs if runtime is not None else 0.0
        speed = runtime.controller.head_speed if runtime is not None else 0.0
        self.recorder.record(
            event_type, description, position, speed,
            train_id=runtime.train_id if runtime is not None else "",
            source=source, severity=severity, entity_id=entity_id,
        )
