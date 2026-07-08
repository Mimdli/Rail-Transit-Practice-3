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

from typing import List, Optional, Tuple
from dataclasses import dataclass

from src.common.track_position import TrackPosition, ITrackQuery
from src.common.car_config import CouplerConfig, DEFAULT_COUPLER_CONFIG
from src.common.car_state import CarState
from src.common.consist import TrainConsist, CONSIST_4M2T
from src.vehicle.forces import IEnvironmentQuery
from src.vehicle.dynamics_pipeline import PerCarDynamicsPipeline
from src.vehicle.force_report import ForceReport, CarForceReport


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
        self.consist = consist or CONSIST_4M2T
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

    # ── 控制指令 ───────────────────────────────────────────────

    def set_throttle(self, level: float):
        """设置牵引手柄位。

        Args:
            level: 牵引力比例 (0.0 ~ 1.0)。
        """
        self.throttle = max(0.0, min(1.0, level))

    def set_brake(self, level: float):
        """设置制动级别。

        Args:
            level: 制动级别 (0.0 ~ 1.0，1.0 = 紧急制动)。
        """
        self.brake_level = max(0.0, min(1.0, level))

    def set_target_speed(self, speed: float):
        """设置目标速度 (m/s)。控制器需自行实现调速逻辑。"""
        self.target_speed = max(0.0, speed)

    def emergency_brake(self):
        """紧急制动：最大制动力。"""
        self.throttle = 0.0
        self.brake_level = 1.0

    def coast(self):
        """惰行：无牵引无制动。"""
        self.throttle = 0.0
        self.brake_level = 0.0

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

        # 构建 ForceReport（传入有效值而非 self.throttle/self.brake_level）
        report = self._build_report(dt, summary, throttle, brake)
        self._history.append(report)

        return report

    def _build_report(self, dt: float, summary: dict,
                      effective_throttle: float, effective_brake: float) -> ForceReport:
        """根据当前状态和 Pipeline 摘要构建 ForceReport。"""
        cars = []
        n = len(self.consist)

        for i in range(n):
            car_config = self.consist[i]
            state = self.states[i]

            # 计算当前力分量（用于报告）
            gradient = self.track.get_gradient(state.position)
            is_tunnel = self.track.get_is_tunnel(state.position)
            adhesion = self.env.get_adhesion_coefficient()

            from src.vehicle.forces import (
                calc_davis_resistance, calc_grade_resistance,
                calc_tunnel_resistance, calc_tractive_force, calc_brake_force,
            )

            f_davis = calc_davis_resistance(car_config, state.velocity)
            f_grade = calc_grade_resistance(car_config, gradient)
            f_tunnel = calc_tunnel_resistance(car_config, state.velocity, is_tunnel)
            f_traction = calc_tractive_force(car_config, state.velocity,
                                              effective_throttle, adhesion)
            f_brake = calc_brake_force(car_config, state.velocity,
                                        effective_brake, adhesion)

            total_ext = f_davis + f_grade + f_tunnel + f_traction + f_brake

            car_report = CarForceReport(
                car_index=i,
                position=state.position,
                velocity=state.velocity,
                acceleration=state.acceleration,
                davis_resistance=f_davis,
                grade_resistance=f_grade,
                tunnel_resistance=f_tunnel,
                tractive_force=f_traction,
                brake_force=f_brake,
                total_external_force=total_ext,
                net_force=total_ext,  # 简化：不含车钩力
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
