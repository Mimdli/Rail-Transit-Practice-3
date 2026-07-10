"""AutoDriveController — 自动驾驶控制器

基于 3 段式精确停车逻辑的自动驾驶控制器，适用于多体列车动力学。

控制策略（每个决策步）:
    1. 巡航段 (distance > stop_distance):
       速度 < 限速 × cruise_speed_factor → MEDIUM_TRACTION
       否则 → COAST
    2. 比例减速段 (emergency_brake_distance < distance ≤ stop_distance):
       目标速度 = approach_speed × (distance / stop_distance)
       超速 (> target + 0.5) → SERVICE_BRAKE
       欠速 (< target - 0.5) → LOW_TRACTION
       接近 (|v - target| ≤ 0.5) → COAST
    3. 紧急制动段 (distance ≤ emergency_brake_distance):
       EMERGENCY_BRAKE 确保精确停车

Usage:
    ctrl = VehicleController(consist, track, env)
    auto = AutoDriveController(ctrl)
    auto.set_target(TrackPosition(segment_id=2, offset=500.0))
    ctrl.set_running_mode(RunningMode.AUTOMATIC)
    while not auto.is_stopped:
        auto.step()          # 决策（设置 throttle/brake）
        ctrl.step(0.033)     # 推进仿真
"""

from typing import Optional, List, TYPE_CHECKING
from src.common.track_position import TrackPosition, ITrackQuery
from src.vehicle.enums import ControlLevel, RunningMode
from src.vehicle.vehicle_controller import VehicleController

if TYPE_CHECKING:
    from src.track.route import Route
    from src.track.data import TrackData


class AutoDriveController:
    """自动驾驶控制器，实现 3 段式精确停车。

    不直接推进仿真——仅根据当前状态做出控制决策。
    调用者需在 auto.step() 之后调用 controller.step(dt) 推进物理仿真。
    """

    def __init__(self, controller: VehicleController,
                 cruise_speed_factor: float = 0.9,
                 stop_distance: float = 20.0,
                 emergency_brake_distance: float = 0.5,
                 approach_speed: float = 5.0):
        """
        Args:
            controller: 被控的 VehicleController 实例。
            cruise_speed_factor: 巡航速度占当前限速的比例 (0.0 ~ 1.0)。
            stop_distance: 开始精确停车的距离阈值 (m)。
            emergency_brake_distance: 触发紧急制动的距离阈值 (m)。
            approach_speed: 接近阶段的基础速度 (m/s)。
        """
        self.controller = controller
        self.cruise_speed_factor = cruise_speed_factor
        self.stop_distance = stop_distance
        self.emergency_brake_distance = emergency_brake_distance
        self.approach_speed = approach_speed

        self.target_position: Optional[TrackPosition] = None
        self._track: Optional[ITrackQuery] = None

        # ── 进路（路线选择） ─────────────────────────────────
        self._route: Optional["Route"] = None
        self._all_routes: List["Route"] = []  # 所有可用进路（供 UI 查询）

    # ── 目标设置 ───────────────────────────────────────────────

    def set_target(self, position: TrackPosition):
        """设置目标停车位置。

        如果当前进路为"自动"模式（seg_ids 为空），自动沿主线
        计算从当前位置到目标的进路。否则沿用已有的手动选择进路。

        Args:
            position: 线路上的目标停车位置（头车应停在此处）。
        """
        self.target_position = position
        self._track = self.controller.track

        # 确保当前进路已同步到 track adapter
        if self._route is not None and not self._route.is_auto:
            if self._track is not None:
                self._track.set_active_route(self._route)
        elif self._route is not None and self._route.is_auto:
            # 自动算路
            self._compute_and_set_route(position)

    # ── 进路管理 ───────────────────────────────────────────────

    def set_route(self, route: "Route"):
        """设置当前进路。

        如果 route.is_auto 为 True，在下次 set_target() 时自动算路。
        否则立即将进路同步到 track adapter。

        Args:
            route: 要设置的进路。
        """
        self._route = route
        # 确保 _track 引用与 controller 同步（controller.track 可能已被替换）
        track = self.controller.track
        if not route.is_auto:
            if track is not None:
                track.set_active_route(route)
                self._track = track
        else:
            # 自动模式：如果已有 target，立即算路
            if self.target_position is not None and track is not None:
                self._track = track
                self._compute_and_set_route(self.target_position)

    @property
    def active_route(self) -> Optional["Route"]:
        """获取当前活动进路。"""
        return self._route

    @property
    def available_routes(self) -> List["Route"]:
        """获取所有可用进路列表（供 UI 显示）。"""
        return list(self._all_routes)

    def set_available_routes(self, routes: List["Route"]):
        """设置可用进路列表。

        Args:
            routes: 所有可用进路（含"自动"模式）。
        """
        self._all_routes = list(routes)
        # 默认选中"自动"
        auto_route = next((r for r in routes if r.is_auto), None)
        if auto_route is not None and self._route is None:
            self._route = auto_route

    # ── 控制步进 ───────────────────────────────────────────────

    def step(self):
        """执行一步自动驾驶控制决策。

        根据头车位置与目标的沿线路距离，选择控制级位。
        调用者应在调用此方法后调用 controller.step(dt) 推进仿真。

        无目标或 track 未初始化时为安全惰行。
        """
        if self.target_position is None or self._track is None:
            return
        if not self.controller.states:
            return

        # 确保处于自动驾驶模式
        if self.controller.running_mode != RunningMode.AUTOMATIC:
            self.controller.set_running_mode(RunningMode.AUTOMATIC)

        head_state = self.controller.states[0]
        distance = self._distance_to_target(head_state.position, self.target_position)

        # 获取头车当前位置的限速
        speed_limit = self._track.get_speed_limit(head_state.position)
        current_speed = self.controller.head_speed

        if distance <= self.emergency_brake_distance:
            # 3. 紧急制动段
            self._apply(ControlLevel.EMERGENCY_BRAKE)
        elif distance < self.stop_distance:
            # 2. 比例减速段
            target_speed = max(
                0.8,
                self.approach_speed * (distance / self.stop_distance),
            )
            speed_deadband = 0.2
            # 低速死区可能让列车停在目标前数米；停车未到位时使用低牵引爬行。
            if current_speed < 0.05 and distance > self.emergency_brake_distance:
                self._apply(ControlLevel.LOW_TRACTION)
            elif current_speed > target_speed + speed_deadband:
                self._apply(ControlLevel.SERVICE_BRAKE)
            elif current_speed < target_speed - speed_deadband:
                self._apply(ControlLevel.LOW_TRACTION)
            else:
                self._apply(ControlLevel.COAST)
        else:
            # 1. 巡航段
            cruise_speed = speed_limit * self.cruise_speed_factor
            if current_speed < cruise_speed:
                self._apply(ControlLevel.MEDIUM_TRACTION)
            else:
                self._apply(ControlLevel.COAST)

    # ── 状态查询 ───────────────────────────────────────────────

    @property
    def is_stopped(self) -> bool:
        """列车是否已停止。"""
        return self.controller.is_stopped

    @property
    def distance_to_target(self) -> Optional[float]:
        """头车到目标停车位置的沿线路距离 (m)。

        正值 = 目标在前方，负值 = 已越过目标。
        未设置目标时返回 None。
        """
        if self.target_position is None or self._track is None:
            return None
        if not self.controller.states:
            return None
        return self._distance_to_target(
            self.controller.states[0].position,
            self.target_position,
        )

    # ── 内部方法 ───────────────────────────────────────────────

    def _compute_and_set_route(self, target_position: TrackPosition):
        """自动计算从当前头车位置到目标的主动进路。

        沿主线 forward neighbor 链找到从头车所在 segment 到
        目标所在 segment 的路径，设置到 track adapter。
        """
        if self._track is None:
            return
        if not self.controller.states:
            return

        from src.track.route import compute_mainline_route
        from src.track.adapter import TrackDataAdapter

        # 需要底层 TrackData 来做路由计算
        if not isinstance(self._track, TrackDataAdapter):
            return  # MockTrackQuery — 无道岔，不需要进路

        td = self._track.track_data
        head_pos = self.controller.states[0].position
        start_seg = head_pos.segment_id
        target_seg = target_position.segment_id

        route = compute_mainline_route(td, start_seg, target_seg)
        if route is not None:
            self._track.set_active_route(route)

    def _apply(self, level: ControlLevel):
        """绕过运行模式检查直接设置控制指令。

        AutoDriveController 需要直接控制 throttle/brake，
        而 apply_control_level 在 MANUAL 模式下会被忽略。
        因此使用 set_throttle/set_brake 直接设置。
        """
        from src.vehicle.enums import CONTROL_LEVEL_MAP
        throttle, brake = CONTROL_LEVEL_MAP[level]
        self.controller.set_throttle(throttle)
        self.controller.set_brake(brake)

    def _distance_to_target(self, current: TrackPosition,
                            target: TrackPosition) -> float:
        """计算沿线路从当前位置到目标的距离 (m)。

        通过 track.to_absolute 实现跨区段距离计算。
        """
        return self.controller.direction * (
            self._track.to_absolute(target) - self._track.to_absolute(current)
        )
