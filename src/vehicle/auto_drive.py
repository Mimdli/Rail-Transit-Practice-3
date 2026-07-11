"""AutoDriveController — 自动驾驶控制器

基于物理制动曲线的精确停车 + 停站状态机（自动开关门/停站时间）。

控制策略（每个决策步）— 连续 P 控制器:
    巡航段 (distance > d_brake):
        speed_error = cruise_speed - current_speed
        throttle = clamp(Kp_cruise × speed_error, 0, 1)
        brake = 0
    制动段 (distance ≤ d_brake):
        物理制动曲线 v_target = sqrt(2 × a_brake × d)
        speed_error = current_speed - v_target
        超速 → brake = clamp(Kp_brake × speed_error, 0, 1)
        欠速 → throttle = clamp(Kp_traction × |error|, 0, 0.3)
        最终 0.5m 内 → 紧急制动

停站状态机:
    CRUISING → APPROACHING → DWELL → DEPARTING → CRUISING → ...
    到站停稳 → 自动开门 → 停站计时 → 自动关门 → 查找下一站 → 发车

进路管理已提取至 RouteManager（src/vehicle/route_manager.py）。
AutoDriveController 通过 route_manager 参数持有 RouteManager 引用，
active_route / available_routes / set_route / set_available_routes
均委托给 RouteManager。

Usage:
    ctrl = VehicleController(consist, track, env)
    rm = RouteManager(track_adapter)
    auto = AutoDriveController(ctrl, interlock, route_manager=rm)
    auto.set_target(TrackPosition(segment_id=2, offset=500.0))
    ctrl.set_running_mode(RunningMode.AUTOMATIC)
    while not auto.is_stopped:
        auto.step(0.033)      # 决策（设置 throttle/brake + 车门）
        ctrl.step(0.033)      # 推进仿真
"""

import math
from typing import Optional, List, TYPE_CHECKING
from src.common.track_position import TrackPosition, ITrackQuery
from src.vehicle.enums import RunningMode, DoorSide, StationPhase
from src.vehicle.vehicle_controller import VehicleController

if TYPE_CHECKING:
    from src.track.route import Route
    from src.track.data import TrackData
    from src.door.interlock import DoorInterlock
    from src.vehicle.route_manager import RouteManager


class AutoDriveController:
    """自动驾驶控制器，实现连续 P 控制 + 停站自动开关门。

    不直接推进仿真——仅根据当前状态做出控制决策。
    调用者需在 auto.step(dt) 之后调用 controller.step(dt) 推进物理仿真。
    """

    # ── P 控制器增益 ──────────────────────────────────────────
    KP_CRUISE = 0.3        # 巡航：误差 3.3 m/s → 全牵引
    KP_BRAKE = 1.5         # 制动：误差 0.67 m/s → 全制动
    KP_TRACTION_APPROACH = 0.15  # 接近段欠速补偿（温和）
    # ── 制动距离安全系数 ─────────────────────────────────────
    BRAKE_MARGIN = 1.3     # 制动距离安全余量（30%），比原 20% 更保守

    def __init__(self, controller: VehicleController,
                 interlock: "DoorInterlock | None" = None,
                 cruise_speed_factor: float = 0.9,
                 approach_speed: float = 5.0,
                 dwell_time: float = 20.0,
                 route_manager: "RouteManager | None" = None):
        """
        Args:
            controller: 被控的 VehicleController 实例。
            interlock: 车门联锁（用于自动开门侧判断和下一站查询）。
            cruise_speed_factor: 巡航速度占当前限速的比例 (0.0 ~ 1.0)。
            approach_speed: 接近阶段的最大初速度 (m/s)，实际由制动曲线决定。
            dwell_time: 停站时间 (s)，可运行时修改。
            route_manager: 进路管理器（可选）。若提供，进路管理委托给
                           RouteManager；否则使用内部存储（向后兼容）。
        """
        self.controller = controller
        self._interlock = interlock
        self.cruise_speed_factor = cruise_speed_factor
        self.approach_speed = approach_speed
        self.dwell_time = dwell_time
        # 调度层仍会调整这些旧参数；新控制器用动态制动曲线，保留属性用于兼容。
        self.stop_distance: float = 20.0
        self.emergency_brake_distance: float = 0.5

        self.target_position: Optional[TrackPosition] = None
        self._track: Optional[ITrackQuery] = None

        # ── 停站状态机 ─────────────────────────────────────────
        self.station_phase: StationPhase = StationPhase.CRUISING
        self._dwell_timer: float = 0.0

        # ── 进路管理（优先使用 RouteManager，否则内部存储） ──────
        self._route_manager = route_manager
        self._route: Optional["Route"] = None  # 向后兼容：无 RouteManager 时使用
        self._all_routes: List["Route"] = []   # 向后兼容：无 RouteManager 时使用

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
        active_route = self.active_route
        if active_route is not None and not active_route.is_auto:
            if self._track is not None:
                self._track.set_active_route(active_route)
        elif active_route is not None and active_route.is_auto:
            # 自动算路
            if self._route_manager is not None and self.controller.states:
                self._route_manager.compute_route_to(
                    position, self.controller.states[0].position)
            elif self._route is not None and self._route.is_auto:
                # 向后兼容：无 RouteManager 时使用旧逻辑
                self._compute_and_set_route(position)

        # 重置停站状态（新目标 = 新一次停车流程）。
        # 但如果正在停站（DWELL），不中断 —— 新目标将在发车后生效，
        # 避免进路变更等操作异常终止当前停站流程。
        if self.station_phase != StationPhase.DWELL:
            self.station_phase = StationPhase.CRUISING
            self._dwell_timer = 0.0

    def reset_state(self):
        """重置自动驾驶状态（切手动时调用）。"""
        self.station_phase = StationPhase.CRUISING
        self._dwell_timer = 0.0
        # 保留 target_position 以便切回自动时可能需要

    # ── 内部控制（绕过模式门禁） ───────────────────────────────

    def _set_throttle(self, level: float):
        """设置牵引力（内部旁路，不受驾驶模式门禁限制）。

        AutoDriveController 在 AUTOMATIC 模式下需要直接控制
        throttle/brake，通过 bypass_mode_check=True 绕过
        TractionBrakeController 的模式检查。
        """
        self.controller.traction.set_throttle(level, bypass_mode_check=True)

    def _set_brake(self, level: float):
        """设置制动力（内部旁路，不受驾驶模式门禁限制）。"""
        self.controller.traction.set_brake(level, bypass_mode_check=True)

    # ── 进路管理（委托给 RouteManager，若无则使用内部存储） ──

    @property
    def route_manager(self) -> "RouteManager | None":
        """获取进路管理器（可能为 None，向后兼容）。"""
        return self._route_manager

    def set_route(self, route: "Route"):
        """设置当前进路。

        优先委托给 RouteManager；若未提供 RouteManager 则使用内部存储。

        Args:
            route: 要设置的进路。
        """
        if self._route_manager is not None:
            self._route_manager.set_route(route)
        else:
            self._route = route
            track = self.controller.track
            if not route.is_auto:
                if track is not None:
                    track.set_active_route(route)
                    self._track = track
            else:
                if self.target_position is not None and track is not None:
                    self._track = track
                    self._compute_and_set_route(self.target_position)

    @property
    def active_route(self) -> Optional["Route"]:
        """获取当前活动进路。"""
        if self._route_manager is not None:
            return self._route_manager.active_route
        return self._route

    @property
    def available_routes(self) -> List["Route"]:
        """获取所有可用进路列表（供 UI 显示）。"""
        if self._route_manager is not None:
            return self._route_manager.available_routes
        return list(self._all_routes)

    def set_available_routes(self, routes: List["Route"]):
        """设置可用进路列表。

        Args:
            routes: 所有可用进路（含"自动"模式）。
        """
        if self._route_manager is not None:
            self._route_manager.set_available_routes(routes)
        else:
            self._all_routes = list(routes)
            auto_route = next((r for r in routes if r.is_auto), None)
            if auto_route is not None and self._route is None:
                self._route = auto_route

    # ── 控制步进 ───────────────────────────────────────────────

    def step(self, dt: float = 0.033):
        """执行一步自动驾驶控制决策。

        根据当前停站阶段分派：
        - CRUISING / APPROACHING: 连续 P 控制器驾驶
        - DWELL: 停站计时 + 自动开门/关门
        - DEPARTING: 查找下一站，切换目标，发车

        Args:
            dt: 仿真步长 (s)，用于停站计时器累加。
        """
        if self.target_position is None or self._track is None:
            return
        if not self.controller.states:
            return

        head_state = self.controller.states[0]
        distance = self._distance_to_target(head_state.position, self.target_position)
        current_speed = self.controller.head_speed

        # ── 停站状态机分派 ──────────────────────────────────────
        if self.station_phase in (StationPhase.CRUISING, StationPhase.APPROACHING):
            self._step_driving(distance, current_speed)
        elif self.station_phase == StationPhase.DWELL:
            self._step_dwell(dt)
        elif self.station_phase == StationPhase.DEPARTING:
            self._step_departing()

    # ═══════════════════════════════════════════════════════════════
    # 驾驶控制（连续 P 控制器）
    # ═══════════════════════════════════════════════════════════════

    def _step_driving(self, distance: Optional[float], current_speed: float):
        """巡航与制动接近阶段 — 前馈 + 反馈控制器。

        制动段使用运动学前馈（v² = 2ad）计算所需制动级位，
        配合 P 反馈修正速度跟踪误差，实现精确停车。

        Args:
            distance: 到头车目标的沿线路距离 (m)。
            current_speed: 头车当前速度 (m/s)。
        """
        if distance is None:
            return

        speed_limit = self._track.get_speed_limit(
            self.controller.states[0].position
        )

        # ── 到站判断 ──────────────────────────────────────────
        if self.controller.is_stopped and distance < 3.0:
            self._enter_dwell()
            return

        # ── 计算制动曲线 ──────────────────────────────────────
        a_brake = self._compute_brake_deceleration()
        d_brake = (current_speed ** 2) / (2.0 * a_brake) * self.BRAKE_MARGIN

        if distance <= max(d_brake, 5.0):
            # ══════════════════════════════════════════════════
            # 制动接近段 — 前馈 + 反馈控制器
            # ══════════════════════════════════════════════════
            self.station_phase = StationPhase.APPROACHING

            # 最终 0.5m：紧急制动确保精确停车
            if distance <= 0.5:
                self._set_throttle(0.0)
                self._set_brake(1.0)
                return

            # ── 前馈：运动学公式 v² = 2ad ──────────────────
            # a_required = v² / (2d)：要想在距离 d 内停稳所需的减速度
            # 前馈制动级位 = a_required / a_available
            required_decel = current_speed ** 2 / (2.0 * max(distance, 0.01))
            feed_forward = min(1.0, required_decel / a_brake)

            # ── 反馈：P 控制器修正速度跟踪误差 ─────────────
            v_target = math.sqrt(2.0 * a_brake * max(0.0, distance))
            v_target = min(v_target, self.approach_speed)
            speed_error = current_speed - v_target
            feedback = self.KP_BRAKE * max(0.0, speed_error)

            # 合成制动指令（前馈为主，反馈为辅）
            brake_cmd = max(0.0, min(1.0, feed_forward + feedback))

            if brake_cmd > 0.01:
                self._set_throttle(0.0)
                self._set_brake(brake_cmd)
            elif speed_error < -0.1:
                # 欠速超过 0.1 m/s → 温和牵引补偿（上限 30%）
                throttle_cmd = min(0.3, self.KP_TRACTION_APPROACH * abs(speed_error))
                self._set_throttle(throttle_cmd)
                self._set_brake(0.0)
            else:
                # 接近目标速度（±0.1 m/s）→ 惰行
                self._set_throttle(0.0)
                self._set_brake(0.0)
        else:
            # ══════════════════════════════════════════════════
            # 巡航段 — P 控制器维持巡航速度
            # ══════════════════════════════════════════════════
            self.station_phase = StationPhase.CRUISING
            cruise_speed = speed_limit * self.cruise_speed_factor
            speed_error = cruise_speed - current_speed

            if speed_error > 0:
                # 低于目标 → 比例牵引
                throttle_cmd = min(1.0, self.KP_CRUISE * speed_error)
                self._set_throttle(throttle_cmd)
                self._set_brake(0.0)
            else:
                # 达到或超过目标 → 惰行（让阻力自然减速）
                self._set_throttle(0.0)
                self._set_brake(0.0)

    # ═══════════════════════════════════════════════════════════════
    # 停站控制（DWELL / DEPARTING）
    # ═══════════════════════════════════════════════════════════════

    def _enter_dwell(self):
        """进入停站阶段：自动开门并开始计时。"""
        self.station_phase = StationPhase.DWELL
        self._dwell_timer = 0.0

        # 自动开门
        if self._interlock is not None:
            side = self._interlock.get_allowed_door_side()
            if side != DoorSide.NONE:
                self.controller.open_door(side)

    def _step_dwell(self, dt: float):
        """停站阶段：累加计时，保持制动，到时关门并切换至发车阶段。

        如果关门失败（如障碍物导致重开门），重置计时器并在 5 秒后重试，
        模拟真实系统的障碍物重开门逻辑。

        Args:
            dt: 仿真步长 (s)。
        """
        # 保持紧急制动防止溜车
        self._set_throttle(0.0)
        self._set_brake(1.0)

        self._dwell_timer += dt

        if self._dwell_timer >= self.dwell_time:
            self.controller.close_door()
            # 检查关门是否成功（真实系统可能因障碍物检测重开门）
            if self.controller.any_door_open:
                # 关门失败 — 重置计时器，5 秒后重试
                self._dwell_timer = max(0.0, self.dwell_time - 5.0)
            else:
                self.station_phase = StationPhase.DEPARTING

    def _step_departing(self):
        """发车阶段：查找下一站并设为目标，发车。

        若前方无车站且列车到达线路端点，自动执行折返操作：
        反转 direction，查找后方最近车站作为新目标，继续运行。

        正向运行 (direction=1) 时"前方" = 里程递增方向；
        反向运行 (direction=-1) 时"前方" = 里程递减方向。
        """
        # 保持制动直到找到下一站目标
        self._set_throttle(0.0)
        self._set_brake(1.0)

        # 根据运行方向选择查找策略
        if self.controller.direction == 1:
            next_station = self.find_next_station()
        else:
            next_station = self._find_station_reverse()

        if next_station is not None:
            # 使用 from_absolute 正确构造 TrackPosition（处理多区段线路）
            target = self._track.from_absolute(next_station.position)
            self.set_target(target)
            # set_target 已将 phase 重置为 CRUISING
        elif self._should_turnaround():
            # 线路端点 → 执行折返
            self._do_turnaround()
        # 无下一站且不满足折返条件：保持停止（已在端点）

    # ═══════════════════════════════════════════════════════════════
    # 折返操作（线路终点自动反向）
    # ═══════════════════════════════════════════════════════════════

    def _should_turnaround(self) -> bool:
        """判断列车是否应执行折返操作。

        条件：列车静止在（或接近）线路端点且前方无车站。
        - 正向运行 (direction=1) → 接近线路终点 (total_length)
        - 反向运行 (direction=-1) → 接近线路起点 (0m)

        Returns:
            True 应执行折返。
        """
        if not self.controller.states or self._track is None:
            return False
        if not self.controller.is_stopped:
            return False

        head_abs = self._track.to_absolute(
            self.controller.states[0].position
        )
        total = self._track.total_length()

        threshold = 80.0  # m：距端点 80m 内触发折返
        if self.controller.direction == 1:
            return head_abs >= total - threshold
        elif self.controller.direction == -1:
            return head_abs <= threshold
        return False

    def _do_turnaround(self):
        """执行折返操作：反转方向，查找后方最近车站为新目标。

        策略：
          1. 反转 controller.direction（正向→负向，负向→正向）
          2. 优先用 get_nearest_station_behind 查找后方最近车站
          3. 若后方无车站（线路起点处），回退到 find_next_station 查前方
          4. 跳过当前站（距离 < 5m 则继续往后/往前找）
          5. 设新目标 → 状态机重置为 CRUISING 继续运行
        """
        # 1. 反转方向
        self.controller.direction *= -1
        track_data = getattr(self._interlock, 'track', None)
        if track_data is None:
            return

        head_abs = self._track.to_absolute(
            self.controller.states[0].position
        )

        # 2. 优先查后方（折返后车头朝向的反方向）
        station = track_data.get_nearest_station_behind(head_abs)
        if station is not None:
            dist = head_abs - station.position  # 正数 = 车在前方
            if dist < 5.0:
                station = track_data.get_nearest_station_behind(
                    station.position - 1.0
                )

        # 3. 后方无车站 → 查前方（折返到起点后，新方向在前方）
        if station is None:
            station = track_data.get_nearest_station_ahead(head_abs)
            if station is not None:
                dist = station.position - head_abs
                if dist < 5.0:
                    station = track_data.get_nearest_station_ahead(
                        station.position + 1.0
                    )

        if station is not None:
            target = self._track.from_absolute(station.position)
            self.set_target(target)
            # set_target 已将 phase 重置为 CRUISING

    def _find_station_reverse(self, head_abs: float = None):
        """反向运行时查找前方（里程递减方向）最近车站。

        与 find_next_station() 对称——反向运行时使用
        get_nearest_station_behind() 查找后方（即前方）车站。

        Args:
            head_abs: 头车绝对位置 (m)。为 None 时自动计算。

        Returns:
            Station 或 None（无符合条件车站）。
        """
        if self._interlock is None:
            return None

        track_data = getattr(self._interlock, 'track', None)
        if track_data is None:
            return None

        if head_abs is None:
            head_abs = 0.0
            if self.controller.states and self._track is not None:
                head_abs = self._track.to_absolute(
                    self.controller.states[0].position
                )

        station = track_data.get_nearest_station_behind(head_abs)
        if station is not None:
            dist = head_abs - station.position  # 正数 = 车在前方
            if dist < 5.0:
                # 当前站 → 继续往后找
                station = track_data.get_nearest_station_behind(
                    station.position - 1.0
                )
        return station

    def find_next_station(self, head_abs: float = None):
        """查找前方最近车站，自动跳过当前站（距离 < 5m）。

        这是"查找下一站"的统一入口，供 AutoDriveController 内部、
        MainWindow.set_driving_mode() 和 ControlPanel 使用。

        Args:
            head_abs: 头车绝对位置 (m)。为 None 时自动从头车状态计算。

        Returns:
            Station 或 None（无前方车站）。
        """
        if self._interlock is None:
            return None

        track_data = getattr(self._interlock, 'track', None)
        if track_data is None:
            return None

        if head_abs is None:
            head_abs = 0.0
            if self.controller.states and self._track is not None:
                head_abs = self._track.to_absolute(
                    self.controller.states[0].position
                )

        # 跳过当前站（很近的车站），找下一站
        station = track_data.get_nearest_station_ahead(head_abs)
        if station is not None:
            dist = station.position - head_abs
            if dist < 5.0:
                # 从当前站位置再往前找
                station = track_data.get_nearest_station_ahead(
                    station.position + 1.0
                )
        return station

    # ═══════════════════════════════════════════════════════════════
    # 制动能力计算
    # ═══════════════════════════════════════════════════════════════

    def _compute_brake_deceleration(self) -> float:
        """根据当前编组计算可用制动减速度 (m/s²)。

        使用 FULL_BRAKE (brake_level=0.7) 作为计算基准，
        包含基本阻力贡献和 15% 安全系数。

        Returns:
            可用制动减速度 (m/s², ≥ 0.5 保证下限)。
        """
        total_mass = 0.0
        total_brake_force = 0.0

        for car in self.controller.consist:
            total_mass += car.mass
            brake_level = 0.7
            max_brake = (car.max_service_brake_force +
                         (car.max_emergency_brake_force -
                          car.max_service_brake_force) * brake_level)
            if hasattr(car, 'base_mass') and car.base_mass > 0:
                max_brake *= car.mass / car.base_mass
            total_brake_force += max_brake

        if total_mass <= 0:
            return 0.5

        a_raw = total_brake_force / total_mass
        a_with_resistance = a_raw + 0.03
        a_effective = a_with_resistance * 0.85

        return max(a_effective, 0.5)

    # ═══════════════════════════════════════════════════════════════
    # 停站时间属性
    # ═══════════════════════════════════════════════════════════════

    @property
    def dwell_remaining(self) -> float:
        """剩余停站时间 (s)。非 DWELL 阶段返回 0。"""
        if self.station_phase != StationPhase.DWELL:
            return 0.0
        return max(0.0, self.dwell_time - self._dwell_timer)

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
        """[向后兼容] 自动计算从当前头车位置到目标的主动进路。

        仅在未提供 RouteManager 时使用。新代码应使用
        RouteManager.compute_route_to()。
        """
        if self._track is None:
            return
        if not self.controller.states:
            return

        from src.track.route import compute_mainline_route
        from src.track.adapter import TrackDataAdapter

        if not isinstance(self._track, TrackDataAdapter):
            return

        td = self._track.track_data
        head_pos = self.controller.states[0].position
        start_seg = head_pos.segment_id
        target_seg = target_position.segment_id

        route = compute_mainline_route(td, start_seg, target_seg)
        if route is not None:
            self._track.set_active_route(route)

    def _distance_to_target(self, current: TrackPosition,
                            target: TrackPosition) -> float:
        """计算沿线路从当前位置到目标的距离 (m)。

        通过 track.to_absolute 实现跨区段距离计算。
        """
        return self.controller.direction * (
            self._track.to_absolute(target) - self._track.to_absolute(current)
        )
