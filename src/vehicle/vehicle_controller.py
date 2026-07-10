"""VehicleController — 顶层控制器

阶段 9：顶层入口，编排仿真循环。

职责:
    1. 持有 TrainConsist（编组）+ ITrackQuery（线路）+ IEnvironmentQuery（环境）
    2. 每步调用 PerCarDynamicsPipeline.step()
    3. 委托 TractionBrakeController 处理牵引/制动控制
    4. 管理车辆运行状态（VehicleState 状态机）
    5. 检查联锁约束（traction_permitted, emergency_brake_required）
    6. 管理仿真时钟和状态历史

注意：此文件为新的多体动力学 VehicleController，不修改现有的
      controller.py（ManualController/AutoController），两者并列存在。

架构（重构后）:
    VehicleController (物理层 + 状态管理)
    ├── TractionBrakeController (牵引/制动执行器) ← NEW
    └── PerCarDynamicsPipeline (动力学仿真)
"""

import copy
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass

from src.common.track_position import TrackPosition, ITrackQuery
from src.common.car_config import CouplerConfig, DEFAULT_COUPLER_CONFIG
from src.common.car_state import CarState
from src.common.consist import TrainConsist, CONSIST_4M2T
from src.vehicle.forces import IEnvironmentQuery
from src.vehicle.dynamics_pipeline import PerCarDynamicsPipeline
from src.vehicle.force_report import ForceReport, CarForceReport
from src.vehicle.enums import DoorSide, RunningMode, ControlLevel, LoadLevel, VehicleState, CONTROL_LEVEL_MAP
from src.vehicle.traction_controller import TractionBrakeController, TractionInterlock


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
        controller.traction.set_throttle(0.8)   # 通过牵引控制器
        controller.set_throttle(0.8)            # 或通过代理（向后兼容）
        for _ in range(100):
            report = controller.step(0.033)
            print(f"v = {report.head_velocity:.1f} m/s")
    """

    def __init__(self,
                 consist: TrainConsist = None,
                 track: ITrackQuery = None,
                 env: IEnvironmentQuery = None,
                 coupler_config: CouplerConfig = None,
                 interlock: MockInterlock = None,
                 speed_limit_tau: float = 1.0):
        """
        Args:
            consist: 列车编组配置，默认 CONSIST_4M2T。
            track: 线路数据查询接口。
            env: 环境数据查询接口。
            coupler_config: 车钩参数。
            interlock: 联锁约束（Mock）。
            speed_limit_tau: 限速软约束时间常数 (s)，传递给 PerCarDynamicsPipeline。
                默认 1.0s。设为 0 恢复硬钳位（不推荐）。
        """
        # 深拷贝编组，确保每个控制器实例的车辆配置完全隔离
        self.consist = copy.deepcopy(consist or CONSIST_4M2T)
        self.track = track
        self.env = env
        self.interlock = interlock or MockInterlock()

        self.pipeline = PerCarDynamicsPipeline(coupler_config,
                                               speed_limit_tau=speed_limit_tau)

        # ── 牵引/制动执行器（NEW） ──────────────────────────────
        self._traction = TractionBrakeController(self.pipeline)

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

    # ═══════════════════════════════════════════════════════════
    # 牵引/制动代理属性（向后兼容）
    # ═══════════════════════════════════════════════════════════

    @property
    def traction(self) -> TractionBrakeController:
        """获取牵引/制动控制器（推荐新代码使用此属性访问）。"""
        return self._traction

    @property
    def throttle(self) -> float:
        """牵引力比例 (0.0 ~ 1.0)。代理到 TractionBrakeController。"""
        return self._traction.throttle

    @throttle.setter
    def throttle(self, value: float):
        self._traction.throttle = max(0.0, min(1.0, value))

    @property
    def brake_level(self) -> float:
        """制动级别 (0.0 ~ 1.0)。代理到 TractionBrakeController。"""
        return self._traction.brake_level

    @brake_level.setter
    def brake_level(self, value: float):
        self._traction.brake_level = max(0.0, min(1.0, value))

    @property
    def target_speed(self) -> float:
        """目标速度 (m/s)。代理到 TractionBrakeController。"""
        return self._traction.target_speed

    @target_speed.setter
    def target_speed(self, value: float):
        self._traction.target_speed = max(0.0, value)

    @property
    def vehicle_state(self) -> VehicleState:
        """车辆运行状态。"""
        return self._traction.vehicle_state

    def set_throttle(self, level: float):
        """设置牵引手柄位（代理）。

        Args:
            level: 牵引力比例 (0.0 ~ 1.0)。
        """
        self._traction.set_throttle(level)

    def set_brake(self, level: float):
        """设置制动级别（代理）。

        Args:
            level: 制动级别 (0.0 ~ 1.0，1.0 = 紧急制动)。
        """
        self._traction.set_brake(level)

    def set_target_speed(self, speed: float):
        """设置目标速度 (m/s)（代理）。"""
        self._traction.set_target_speed(speed)

    def emergency_brake(self):
        """紧急制动（代理）。"""
        self._traction.command_emergency()

    def coast(self):
        """惰行（代理）。"""
        self._traction.command_coast()

    def apply_control_level(self, level: ControlLevel):
        """将离散司控器级位映射为连续 throttle/brake 并应用（代理）。

        仅在 MANUAL 模式下生效。AUTOMATIC 模式下调用将被忽略，
        此时应由 AutoDriveController 直接设置 throttle/brake。
        """
        if self.running_mode != RunningMode.MANUAL:
            return
        self._traction.apply_control_level(level)

    # ═══════════════════════════════════════════════════════════
    # 车辆状态管理（NEW）
    # ═══════════════════════════════════════════════════════════

    def startup(self) -> bool:
        """初始化完成 → STOPPED。

        在创建控制器并设置好线路/环境后调用，将车辆置于
        就绪状态（停止中，施加保持制动）。

        Returns:
            True 如果成功进入 STOPPED 状态。
        """
        if self._traction.vehicle_state != VehicleState.INIT:
            return False
        self._traction.vehicle_state = VehicleState.STOPPED
        # 确保牵引归零（制动由调用者按需设置）
        self._traction.throttle = 0.0
        return True

    def start_moving(self, throttle_level: float = 1.0) -> bool:
        """发车：STOPPED → STARTING。

        联锁条件：所有车门必须关闭，牵引必须被允许。

        Args:
            throttle_level: 初始牵引力比例 (0.0 ~ 1.0)，默认全牵引。

        Returns:
            True 如果发车指令被接受。
        """
        if self._traction.vehicle_state not in (VehicleState.STOPPED, VehicleState.INIT):
            return False
        # 联锁检查
        if self.any_door_open:
            return False
        if not self.interlock.traction_permitted:
            return False
        if self.interlock.emergency_brake_required:
            return False

        self._traction.command_start()
        self._traction.set_throttle(throttle_level)
        return True

    def command_stop(self, brake_level: float = 0.4):
        """停车：施加常用制动，目标 STOPPED。

        Args:
            brake_level: 制动级别，默认 0.4（常用制动）。
        """
        self._traction.command_stop(brake_level)

    def command_emergency(self):
        """紧急制动：任何状态 → EMERGENCY。"""
        self._traction.command_emergency()

    # ═══════════════════════════════════════════════════════════
    # 重置与快照（NEW）
    # ═══════════════════════════════════════════════════════════

    def reset_to(self, position: TrackPosition, velocity: float = 0.0,
                 pre_tension: bool = True):
        """重置列车到指定线路位置。

        所有车从头到尾依次排列在目标位置后方。
        当 pre_tension=True 时，车钩预紧到 slack 边界。

        Args:
            position: 头车的目标位置。
            velocity: 初始速度 (m/s)，默认 0。
            pre_tension: 是否预紧车钩。
        """
        slack = self.pipeline.coupler_config.slack if pre_tension else 0.0
        self.states = []
        head_offset = position.offset
        for i, car_config in enumerate(self.consist):
            offset = head_offset - i * (car_config.length + slack)
            self.states.append(CarState(
                position=TrackPosition(
                    segment_id=position.segment_id,
                    offset=offset,
                ),
                velocity=velocity,
                acceleration=0.0,
            ))

        # 重置仿真时钟
        self.sim_time = 0.0
        self.step_count = 0
        self._history.clear()

        # 重置牵引/制动控制器
        self._traction.reset()

        # 保持制动（如果速度为 0）
        if velocity == 0.0:
            self._traction.vehicle_state = VehicleState.STOPPED
            self._traction.brake_level = 0.4
        else:
            self._traction.vehicle_state = VehicleState.MOVING

        # 重置车门和运行模式
        self.left_door_open = False
        self.right_door_open = False
        self.door_side = DoorSide.NONE
        self.set_running_mode(RunningMode.MANUAL)

    def reset_to_station(self, station_index: int = 0) -> bool:
        """重置到指定车站位置。

        Args:
            station_index: 车站索引（0 = 第一个车站）。

        Returns:
            True 如果成功。
        """
        if self.track is None:
            return False

        # 通过 track 获取车站列表
        track_data = getattr(self.track, 'track_data', None)
        if track_data is None:
            return False

        stations = getattr(track_data, 'stations', [])
        if not stations or station_index >= len(stations):
            return False

        station = stations[station_index]
        abs_pos = station.position if hasattr(station, 'position') else station.abs_pos
        return self.reset_to_absolute(abs_pos)

    def reset_to_absolute(self, abs_position: float) -> bool:
        """重置到线路绝对位置 (m)。

        Args:
            abs_position: 线路上从头算起的绝对距离 (m)。

        Returns:
            True 如果成功。
        """
        if self.track is None:
            return False

        try:
            pos = self.track.from_absolute(abs_position)
        except Exception:
            return False

        self.reset_to(pos)
        return True

    def snapshot(self) -> Dict[str, Any]:
        """创建当前完整状态快照（用于保存/恢复）。

        Returns:
            包含所有必要状态的字典。可传给 restore_snapshot() 恢复。
        """
        return {
            'states': copy.deepcopy(self.states),
            'sim_time': self.sim_time,
            'step_count': self.step_count,
            'throttle': self._traction.throttle,
            'brake_level': self._traction.brake_level,
            'target_speed': self._traction.target_speed,
            'vehicle_state': self._traction.vehicle_state,
            'left_door_open': self.left_door_open,
            'right_door_open': self.right_door_open,
            'door_side': self.door_side,
            'running_mode': self.running_mode,
            'filtered_throttle': self.pipeline.filtered_throttle,
            'filtered_brake': self.pipeline.filtered_brake,
        }

    def restore_snapshot(self, snap: Dict[str, Any]):
        """从快照恢复状态。

        Args:
            snap: snapshot() 返回的字典。
        """
        self.states = snap['states']
        self.sim_time = snap['sim_time']
        self.step_count = snap['step_count']
        self._traction.throttle = snap['throttle']
        self._traction.brake_level = snap['brake_level']
        self._traction.target_speed = snap['target_speed']
        self._traction.vehicle_state = snap['vehicle_state']
        self.left_door_open = snap['left_door_open']
        self.right_door_open = snap['right_door_open']
        self.door_side = snap['door_side']
        self.running_mode = snap['running_mode']
        self.pipeline.filtered_throttle = snap['filtered_throttle']
        self.pipeline.filtered_brake = snap['filtered_brake']
        self._history.clear()

    # ═══════════════════════════════════════════════════════════
    # 状态管理（原有，保留）
    # ═══════════════════════════════════════════════════════════

    def replace_consist(self, new_consist: 'TrainConsist',
                        track: 'ITrackQuery' = None):
        """替换编组配置，保持当前头车位置不变。

        Args:
            new_consist: 新的编组配置。
            track: 可选，线路查询接口（用于计算头车位置）。
        """
        self.consist = copy.deepcopy(new_consist)
        if track is not None:
            self.track = track
        if self.states:
            head_pos = self.states[0].position
            start_seg = head_pos.segment_id
            start_off = head_pos.offset
        else:
            start_seg = 1
            start_off = 0.0
        self.reset_states(start_segment_id=start_seg, start_offset=start_off)

    def reset_states(self, start_segment_id: int = 1, start_offset: float = 0.0,
                     pre_tension: bool = True):
        """（重新）初始化各节车状态。

        所有车从头到尾依次排列在起始位置。
        当 pre_tension=True 时，车钩预紧到 slack 边界，模拟"列车已消除间隙、准备发车"状态，
        避免初始 slack take-up 产生的冲击力。

        推荐新代码使用 reset_to() 以支持更灵活的重置。
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

        # 重置牵引/制动控制器
        self._traction.reset()
        self._traction.vehicle_state = VehicleState.STOPPED
        # 注意：不预设 brake_level，由调用者根据需要设置
        # 保持制动应在高层指令（command_stop / start_moving）中体现

        # 重置车门和运行模式
        self.left_door_open = False
        self.right_door_open = False
        self.door_side = DoorSide.NONE
        self.set_running_mode(RunningMode.MANUAL)

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

        同步到 TractionBrakeController 以实现模式门禁：
        AUTOMATIC 模式下 UI 直接调用的 set_throttle/set_brake 被忽略。

        Args:
            mode: RunningMode.MANUAL 或 RunningMode.AUTOMATIC。
        """
        self.running_mode = mode
        self._traction.set_driving_mode(mode)

    # ── 载荷管理 ───────────────────────────────────────────────

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

    # ═══════════════════════════════════════════════════════════
    # 仿真步进
    # ═══════════════════════════════════════════════════════════

    def step(self, dt: float = 0.033) -> ForceReport:
        """推进一个仿真步。

        Args:
            dt: 仿真步长 (s)，默认 0.033s (30fps)。

        Returns:
            ForceReport: 本步的完整力学报告。
        """
        # 联锁约束 → 有效 throttle/brake
        traction_interlock = TractionInterlock(
            traction_permitted=self.interlock.traction_permitted,
            emergency_brake_required=self.interlock.emergency_brake_required,
            door_open=self.any_door_open,
        )
        throttle, brake = self._traction.get_effective_command(traction_interlock)

        # 调用 Pipeline
        self.states, summary = self.pipeline.step(
            self.consist, self.states, dt,
            self.track, self.env,
            throttle, brake,
        )

        # 更新时钟
        self.sim_time += dt
        self.step_count += 1

        # 更新车辆运行状态（自动转换）
        self._traction.update_vehicle_state(
            is_stopped=self.is_stopped,
            head_speed=self.head_speed,
        )

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

    # ═══════════════════════════════════════════════════════════
    # 历史查询
    # ═══════════════════════════════════════════════════════════

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
