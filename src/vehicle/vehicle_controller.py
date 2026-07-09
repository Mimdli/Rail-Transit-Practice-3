"""VehicleController — 顶层控制器

阶段 9：顶层入口，编排仿真循环。

职责:
    1. 持有 TrainConsist（编组）+ ITrackQuery（线路）+ IEnvironmentQuery（环境）
    2. 每步调用 PerCarDynamicsPipeline.step()
    3. 接收控制指令（throttle, brake_level, target_speed）
    4. 检查联锁约束（traction_permitted, emergency_brake_required）
    5. 管理仿真时钟和状态历史

注意：此文件为新的多体动力学 VehicleController，不修改现有的
      controller.py（ManualController/AutoController），两者并列存在。
"""

import copy
from typing import List, Optional, Tuple
from dataclasses import dataclass

from src.common.track_position import TrackPosition, ITrackQuery
from src.common.car_config import CouplerConfig, DEFAULT_COUPLER_CONFIG
from src.common.car_state import CarState
from src.common.consist import TrainConsist, CONSIST_4M2T
from src.vehicle.forces import IEnvironmentQuery
from src.vehicle.dynamics_pipeline import PerCarDynamicsPipeline
from src.vehicle.force_report import ForceReport, CarForceReport
from src.vehicle.enums import DoorSide, RunningMode, ControlLevel, LoadLevel, CONTROL_LEVEL_MAP


# ═══════════════════════════════════════════════════════════════
# 联锁约束（Mock，开发期间使用）
# ═══════════════════════════════════════════════════════════════

@dataclass
class MockInterlock:
    """Mock 联锁约束，开发期间使用。

    集成时替换为真实联锁模块。
    """
    traction_permitted: bool = True
    door_open_permitted: bool = False
    emergency_brake_required: bool = False


# ═══════════════════════════════════════════════════════════════
# VehicleController
# ═══════════════════════════════════════════════════════════════

class VehicleController:
    """多体列车动力学顶层控制器。

    编排仿真循环，管理编组状态和控制指令。

    Usage:
        controller = VehicleController(consist, track, env)
        controller.set_throttle(0.8)
        for _ in range(100):
            report = controller.step(0.033)
            print(f"v = {report.head_velocity:.1f} m/s")
    """

    def __init__(self,
                 consist: TrainConsist = None,
                 track: ITrackQuery = None,
                 env: IEnvironmentQuery = None,
                 coupler_config: CouplerConfig = None,
                 interlock: MockInterlock = None):
        """
        Args:
            consist: 列车编组配置，默认 CONSIST_4M2T。
            track: 线路数据查询接口。
            env: 环境数据查询接口。
            coupler_config: 车钩参数。
            interlock: 联锁约束（Mock）。
        """
        # 深拷贝编组，确保每个控制器实例的车辆配置完全隔离
        self.consist = copy.deepcopy(consist or CONSIST_4M2T)
        self.track = track
        self.env = env
        self.interlock = interlock or MockInterlock()

        self.pipeline = PerCarDynamicsPipeline(coupler_config)

        # 控制指令
        self.throttle: float = 0.0       # 0.0 ~ 1.0
        self.brake_level: float = 0.0    # 0.0 ~ 1.0
        self.target_speed: float = 0.0   # m/s（目标速度，0 表示不限速）

        # 仿真时钟
        self.sim_time: float = 0.0
        self.step_count: int = 0

        # 状态
        self.states: List[CarState] = []
        self._history: List[ForceReport] = []

        # 车门状态（整列车统一控制）
        self.left_door_open: bool = False
        self.right_door_open: bool = False
        self.door_side: DoorSide = DoorSide.NONE

        # 运行模式
        self.running_mode: RunningMode = RunningMode.MANUAL

        # 初始化状态
        self.reset_states()

    # ── 状态管理 ───────────────────────────────────────────────

    def reset_states(self, start_segment_id: int = 1, start_offset: float = 0.0,
                     pre_tension: bool = True):
        """（重新）初始化各节车状态。

        所有车从头到尾依次排列在起始位置。
        当 pre_tension=True 时，车钩预紧到 slack 边界，模拟"列车已消除间隙、准备发车"状态，
        避免初始 slack take-up 产生的冲击力。
        """
        slack = self.pipeline.coupler_config.slack if pre_tension else 0.0
        self.states = []
        # 头车 (i=0) 在最前方（最大 offset），后续车依次在后方（递减 offset）
        head_offset = start_offset
        for i, car_config in enumerate(self.consist):
            offset = head_offset - i * (car_config.length + slack)
            self.states.append(CarState(
                position=TrackPosition(segment_id=start_segment_id, offset=offset),
                velocity=0.0,
                acceleration=0.0,
            ))
        self.sim_time = 0.0
        self.step_count = 0
        self._history.clear()

        # 重置控制指令和滤波器状态
        self.throttle = 0.0
        self.brake_level = 0.0
        self.target_speed = 0.0
        self.pipeline.reset_filters(throttle=True, brake=True)

        # 重置车门和运行模式
        self.left_door_open = False
        self.right_door_open = False
        self.door_side = DoorSide.NONE
        self.running_mode = RunningMode.MANUAL

    # ── 控制指令 ───────────────────────────────────────────────

    def set_throttle(self, level: float):
        """设置牵引手柄位。

        Args:
            level: 牵引力比例 (0.0 ~ 1.0)。
        """
        self.throttle = max(0.0, min(1.0, level))
        # 施加牵引时立即释放制动滤波器，避免 PT1 衰减延迟
        if level > 0:
            self.pipeline.reset_filters(throttle=False, brake=True)

    def set_brake(self, level: float):
        """设置制动级别。

        Args:
            level: 制动级别 (0.0 ~ 1.0，1.0 = 紧急制动)。
        """
        self.brake_level = max(0.0, min(1.0, level))
        # 制动归零时立即重置滤波器，模拟制动缸快速排风
        if level == 0.0:
            self.pipeline.reset_filters(throttle=False, brake=True)
        # 施加制动时重置牵引滤波器
        if level > 0:
            self.pipeline.reset_filters(throttle=True, brake=False)

    def set_target_speed(self, speed: float):
        """设置目标速度 (m/s)。控制器需自行实现调速逻辑。"""
        self.target_speed = max(0.0, speed)

    def set_load_level(self, level: LoadLevel):
        """设置整列车载荷等级，更新各节车的有效质量。

        各载荷等级对应的质量定义在 CarConfig 中（aw0_mass ~ aw3_mass）。
        切换后制动负荷补偿会自动生效（calc_brake_force 根据 base_mass 调整）。

        Args:
            level: 载荷等级（AW0/AW1/AW2/AW3）。
        """
        mass_attr = {
            LoadLevel.AW0: 'aw0_mass',
            LoadLevel.AW1: 'aw1_mass',
            LoadLevel.AW2: 'aw2_mass',
            LoadLevel.AW3: 'aw3_mass',
        }.get(level)
        if mass_attr is None:
            return
        for car in self.consist:
            new_mass = getattr(car, mass_attr, car.mass)
            object.__setattr__(car, 'mass', new_mass)

    def emergency_brake(self):
        """紧急制动：最大制动力。"""
        self.throttle = 0.0
        self.brake_level = 1.0
        # 紧急制动时立即重置牵引滤波器，确保牵引力立即归零
        self.pipeline.reset_filters(throttle=True, brake=False)

    def coast(self):
        """惰行：无牵引无制动。"""
        self.throttle = 0.0
        self.brake_level = 0.0
        # 惰行时重置全部滤波器，确保牵引和制动立即归零
        self.pipeline.reset_filters(throttle=True, brake=True)

    # ── 车门控制 ───────────────────────────────────────────────

    def open_door(self, side: DoorSide) -> bool:
        """打开指定侧车门。

        联锁条件：列车必须完全停止（所有车厢速度 < 0.01 m/s）。

        Args:
            side: 要打开的车门侧（DoorSide.LEFT 或 DoorSide.RIGHT）。

        Returns:
            True 如果开门成功，False 如果因速度联锁被拒绝。
        """
        if not self.is_stopped:
            return False
        if side == DoorSide.LEFT:
            self.left_door_open = True
        elif side == DoorSide.RIGHT:
            self.right_door_open = True
        else:
            return False
        self.door_side = side
        return True

    def close_door(self):
        """关闭所有车门。无速度联锁，任何时候均可关闭。"""
        self.left_door_open = False
        self.right_door_open = False
        self.door_side = DoorSide.NONE

    def doors_closed(self) -> bool:
        """检查所有车门是否关闭。"""
        return not self.left_door_open and not self.right_door_open

    @property
    def any_door_open(self) -> bool:
        """检查是否有任何车门处于打开状态。"""
        return self.left_door_open or self.right_door_open

    # ── 运行模式 ───────────────────────────────────────────────

    def set_running_mode(self, mode: RunningMode):
        """设置运行模式。

        Args:
            mode: RunningMode.MANUAL 或 RunningMode.AUTOMATIC。
        """
        self.running_mode = mode

    # ── 离散控制级位 ───────────────────────────────────────────

    def apply_control_level(self, level: ControlLevel):
        """将离散司控器级位映射为连续 throttle/brake 并应用。

        仅在 MANUAL 模式下生效。AUTOMATIC 模式下调用将被忽略，
        此时应由 AutoDriveController 直接设置 throttle/brake。

        Args:
            level: 司控器级位（ControlLevel 枚举值）。
        """
        if self.running_mode != RunningMode.MANUAL:
            return
        if level not in CONTROL_LEVEL_MAP:
            return
        throttle, brake = CONTROL_LEVEL_MAP[level]
        self.set_throttle(throttle)
        self.set_brake(brake)

    # ── 仿真步进 ───────────────────────────────────────────────

    def step(self, dt: float = 0.033) -> ForceReport:
        """推进一个仿真步。

        Args:
            dt: 仿真步长 (s)，默认 0.033s (30fps)。

        Returns:
            ForceReport: 本步的完整力学报告。
        """
        # 联锁约束检查
        throttle = self.throttle
        brake = self.brake_level

        if self.interlock.emergency_brake_required:
            throttle = 0.0
            brake = 1.0
        elif not self.interlock.traction_permitted:
            throttle = 0.0

        # 调用 Pipeline
        self.states, summary = self.pipeline.step(
            self.consist, self.states, dt,
            self.track, self.env,
            throttle, brake,
        )

        # 更新时钟
        self.sim_time += dt
        self.step_count += 1

        # 构建 ForceReport（使用 Pipeline 滤波后的有效值）
        report = self._build_report(dt, summary)
        self._history.append(report)

        return report

    def _build_report(self, dt: float, summary: dict) -> ForceReport:
        """根据当前状态和 Pipeline 摘要构建 ForceReport。

        使用 Pipeline 滤波后的 throttle/brake 值（而非原始指令），
        确保报告中的力分量与实际物理仿真一致。
        """
        cars = []
        n = len(self.consist)

        # 使用 Pipeline 滤波后的有效值
        effective_throttle = summary.get("filtered_throttle", 0.0)
        effective_brake = summary.get("filtered_brake", 0.0)
        t_limited_list = summary.get("traction_limited", [False] * n)
        b_limited_list = summary.get("brake_limited", [False] * n)
        electric_brake_list = summary.get("electric_brake_forces", [0.0] * n)
        friction_brake_list = summary.get("friction_brake_forces", [0.0] * n)
        coupler_forces = summary.get("coupler_forces", [])
        net_forces_summary = summary.get("net_forces", [0.0] * n)

        for i in range(n):
            car_config = self.consist[i]
            state = self.states[i]

            # 计算当前力分量（用于报告）
            gradient = self.track.get_gradient(state.position)
            is_tunnel = self.track.get_is_tunnel(state.position)
            curve_radius = self.track.get_curve_radius(state.position)
            adhesion = self.env.get_adhesion_coefficient(self.states[0].position)

            from src.vehicle.forces import (
                calc_davis_resistance, calc_grade_resistance,
                calc_tunnel_resistance, calc_curve_resistance,
                calc_tractive_force, calc_brake_force,
                calc_electric_brake_magnitude, calc_electric_brake_force,
                calc_friction_brake_force,
            )

            f_davis = calc_davis_resistance(car_config, state.velocity)
            f_grade = calc_grade_resistance(car_config, gradient)
            f_tunnel = calc_tunnel_resistance(car_config, state.velocity, is_tunnel)
            f_curve = calc_curve_resistance(car_config, curve_radius)
            f_traction, t_limited = calc_tractive_force(car_config, state.velocity,
                                                         effective_throttle, adhesion)
            f_brake_total, b_limited = calc_brake_force(car_config, state.velocity,
                                                         effective_brake, adhesion)
            # 电空拆分（使用带符号的辅助函数，无需手动计算符号）
            e_mag = calc_electric_brake_magnitude(car_config, state.velocity,
                                                    effective_brake)
            f_electric = calc_electric_brake_force(car_config, state.velocity,
                                                    effective_brake)
            f_friction = calc_friction_brake_force(car_config, state.velocity,
                                                    effective_brake, e_mag)

            total_ext = f_davis + f_grade + f_tunnel + f_curve + f_traction + f_brake_total

            # ── 车钩力 ─────────────────────────────────────────────
            # coupler_forces[j] 是 car j（前）与 car j+1（后）之间的车钩力
            # 正值 = 拉伸（前车被向后拉，后车被向前拉）
            coupler_front = coupler_forces[i - 1] if i > 0 else 0.0       # 与前方车的车钩力
            coupler_rear = coupler_forces[i] if i < n - 1 else 0.0        # 与后方车的车钩力
            net_coupler = coupler_front - coupler_rear  # 对自身合力：前拉 + 后推（反作用力）

            car_report = CarForceReport(
                car_index=i,
                position=state.position,
                velocity=state.velocity,
                acceleration=state.acceleration,
                davis_resistance=f_davis,
                grade_resistance=f_grade,
                tunnel_resistance=f_tunnel,
                curve_resistance=f_curve,
                tractive_force=f_traction,
                brake_force=f_brake_total,
                electric_brake_force=f_electric,
                friction_brake_force=f_friction,
                traction_limited=t_limited,
                brake_limited=b_limited,
                coupler_force_front=coupler_front,
                coupler_force_rear=coupler_rear,
                net_coupler_force=net_coupler,
                total_external_force=total_ext,
                net_force=total_ext + net_coupler,
            )
            cars.append(car_report)

        return ForceReport(
            step=self.step_count,
            timestamp=self.sim_time,
            dt=dt,
            n_substeps=summary.get("n_substeps", 0),
            cars=cars,
        )

    # ── 历史查询 ───────────────────────────────────────────────

    @property
    def history(self) -> List[ForceReport]:
        """返回仿真历史（每个外部步的 ForceReport 列表）。"""
        return list(self._history)

    @property
    def last_report(self) -> Optional[ForceReport]:
        """返回最近一步的 ForceReport。"""
        return self._history[-1] if self._history else None

    @property
    def head_speed(self) -> float:
        """头车速度 (m/s)。"""
        if self.states:
            return self.states[0].velocity
        return 0.0

    @property
    def head_speed_kmh(self) -> float:
        """头车速度 (km/h)。"""
        return self.head_speed * 3.6

    @property
    def is_stopped(self) -> bool:
        """所有车是否已停止。"""
        return all(s.velocity < 0.01 for s in self.states)
