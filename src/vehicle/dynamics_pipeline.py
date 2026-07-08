"""PerCarDynamicsPipeline — 微步长七步仿真闭环

阶段 7：接收外部步长 dt，内部切分为微步长 dt_phy，逐微步执行七步闭环。

核心架构决策 —— 物理-UI 时钟解耦：
    - 外部调用者传入 dt（如 0.033s for 30fps）
    - Pipeline 内部将 dt 切分为 N 个 ≤ 0.001s 的微步
    - 微步长硬约束由车钩力刚性方程的数值稳定性条件决定
    - 外部调用者完全感知不到微步长的存在
"""

import math
from typing import List, Tuple, Optional
from src.common.car_config import CarConfig, CouplerConfig, DEFAULT_COUPLER_CONFIG
from src.common.car_state import CarState
from src.common.consist import TrainConsist
from src.common.track_position import ITrackQuery
from src.vehicle.forces import (
    calc_total_resistance, calc_tractive_force, calc_brake_force,
    calc_electric_brake_magnitude, calc_friction_brake_magnitude,
    IEnvironmentQuery,
)
from src.vehicle.coupler import _calc_coupler_force_raw


class PerCarDynamicsPipeline:
    """多体列车动力学仿真管线。

    每个外部仿真步内部自动执行微步长解算：
        n_substeps = ceil(dt / DT_PHY_MAX)
        dt_phy = dt / n_substeps

    七步闭环（每个微步内）:
        1. 查询线路（每节车当前位置的限速/坡度/隧道）
        2. 聚合单车外力（复用预计算的外部力）
        3. 计算车钩力（每个微步内必须重新计算！）
        4. 聚合单车合力
        5. 积分更新状态（使用微步长 dt_phy）
        6. 限速裁剪
        7. （循环结束后）生成报告
    """

    DT_PHY_MAX = 0.001  # 微步长上限（秒），硬约束，不可调大

    def __init__(self, coupler_config: CouplerConfig = None,
                 tau_traction: float = 0.3, tau_brake: float = 0.5):
        """
        Args:
            coupler_config: 车钩参数配置，默认使用 DEFAULT_COUPLER_CONFIG。
            tau_traction: 牵引作动时间常数 (s)，模拟电机建磁迟滞，典型值 0.3s。
            tau_brake: 制动作动时间常数 (s)，模拟气缸充气迟滞，典型值 0.5s。
        """
        self.coupler_config = coupler_config or DEFAULT_COUPLER_CONFIG
        self.step_counter: int = 0

        # PT1 一阶低通滤波器状态（作动迟滞建模）
        self.filtered_throttle: float = 0.0
        self.filtered_brake: float = 0.0
        self.tau_traction: float = tau_traction
        self.tau_brake: float = tau_brake

    def step(self, consist: TrainConsist, states: List[CarState],
             dt: float, track: ITrackQuery, env: IEnvironmentQuery,
             throttle: float = 0.0, brake_level: float = 0.0
             ) -> Tuple[List[CarState], dict]:
        """执行一个外部仿真步。

        Args:
            consist: 列车编组配置。
            states: 各节车的当前状态（长度必须与编组一致）。
            dt: 外部步长（秒），例如 0.033s（30fps）。
            track: 线路数据查询接口。
            env: 环境数据查询接口。
            throttle: 牵引手柄位 (0.0 ~ 1.0)。
            brake_level: 制动级别 (0.0 ~ 1.0)。

        Returns:
            (new_states, summary) — 更新后的状态列表和本步摘要。
        """
        n_cars = len(consist)
        if len(states) != n_cars:
            raise ValueError(f"states 数量 ({len(states)}) 与编组 ({n_cars}) 不匹配")

        adhesion = env.get_adhesion_coefficient(states[0].position)

        # ── PT1 一阶低通滤波（作动迟滞） ─────────────────────
        # 模拟真实制动气缸充气和电机建磁的时间延迟。
        # 滤波器在外步层面更新一次，微步循环内使用滤波后的常值。
        if self.tau_traction > 0:
            alpha_t = 1.0 - math.exp(-dt / self.tau_traction)
            self.filtered_throttle += (throttle - self.filtered_throttle) * alpha_t
        else:
            self.filtered_throttle = throttle

        if self.tau_brake > 0:
            alpha_b = 1.0 - math.exp(-dt / self.tau_brake)
            self.filtered_brake += (brake_level - self.filtered_brake) * alpha_b
        else:
            self.filtered_brake = brake_level

        eff_throttle = self.filtered_throttle
        eff_brake = self.filtered_brake

        # ── 微步长切分 ─────────────────────────────────────────
        n_substeps = max(1, math.ceil(dt / self.DT_PHY_MAX))
        dt_phy = dt / n_substeps

        # ── 计算绝对位置（用于车钩力计算） ─────────────────────
        abs_positions = [track.to_absolute(s.position) for s in states]

        # ── 微步长循环 ─────────────────────────────────────────
        for _ in range(n_substeps):
            # Step 1 — 查询线路（限速/坡度/隧道，位置变化后需更新）
            speed_limits = [
                track.get_speed_limit(s.position) for s in states
            ]

            # Step 2 — 计算外部力（每微步重新计算，因速度/位置已变化）
            external_forces, t_limited, b_limited, electric_brakes, friction_brakes = \
                self._precompute_external_forces(
                    consist, states, track, eff_throttle, eff_brake, adhesion
                )

            # Step 3 — 计算车钩力（每个微步内必须重新计算！）
            coupler_forces = self._calc_all_coupler_forces(
                consist, states, abs_positions
            )

            # Step 4 — 聚合单车合力
            net_forces = self._calc_net_forces(
                external_forces, coupler_forces, n_cars
            )

            # Step 5 — 积分更新状态（使用微步长 dt_phy）
            for i in range(n_cars):
                car = states[i]
                # 回转质量系数：等效质量 = 静质量 × rotary_mass_factor
                effective_mass = consist[i].mass * consist[i].rotary_mass_factor
                a = net_forces[i] / effective_mass
                car.velocity += a * dt_phy
                # 速度不能为负（列车不倒行，除非特殊情况）
                if car.velocity < 0.0:
                    car.velocity = 0.0
                # 更新位置
                abs_positions[i] += car.velocity * dt_phy
                car.position = track.from_absolute(abs_positions[i])
                car.acceleration = a

            # Step 6 — 限速裁剪
            for i in range(n_cars):
                limit = speed_limits[i]
                if states[i].velocity > limit:
                    states[i].velocity = limit

        # ── Step 7 — 生成摘要 ──────────────────────────────────
        # 微步循环中最后一步的车钩力和合力（用于 ForceReport）
        last_coupler_forces = coupler_forces  # 微步循环最后一次迭代的值
        last_net_forces = net_forces

        self.step_counter += 1
        summary = {
            "step": self.step_counter,
            "dt": dt,
            "n_substeps": n_substeps,
            "dt_phy": dt_phy,
            "final_speed": [s.velocity for s in states],
            "final_position": [s.position for s in states],
            "filtered_throttle": self.filtered_throttle,
            "coupler_forces": last_coupler_forces,
            "net_forces": last_net_forces,
            "filtered_brake": self.filtered_brake,
            "traction_limited": t_limited,
            "brake_limited": b_limited,
            "electric_brake_forces": electric_brakes,
            "friction_brake_forces": friction_brakes,
        }
        return states, summary

    # ── 内部方法 ───────────────────────────────────────────────

    def _precompute_external_forces(self, consist, states, track,
                                     throttle, brake, adhesion):
        """预计算外部力（阻力+牵引力+电空制动）。

        黏着限制对总制动力统一施加（通过 calc_brake_force）。
        电空拆分仅用于报告（通过 magnitude 函数）。

        Returns:
            (forces, traction_limited, brake_limited,
             electric_magnitudes, friction_magnitudes)
        """
        forces = []
        traction_limited_list = []
        brake_limited_list = []
        electric_mag_list = []
        friction_mag_list = []
        for i, car_config in enumerate(consist):
            s = states[i]
            gradient = track.get_gradient(s.position)
            is_tunnel = track.get_is_tunnel(s.position)
            curve_radius = track.get_curve_radius(s.position)

            f_resistance = calc_total_resistance(car_config, s.velocity,
                                                  gradient, is_tunnel, curve_radius)
            f_traction, t_limited = calc_tractive_force(car_config, s.velocity,
                                                         throttle, adhesion)
            f_brake, b_limited = calc_brake_force(car_config, s.velocity,
                                                   brake, adhesion)

            # 电空拆分（仅用于报告，不参与合力计算）
            electric_mag = calc_electric_brake_magnitude(car_config, s.velocity, brake)
            friction_mag = calc_friction_brake_magnitude(car_config, brake, electric_mag)

            forces.append(f_resistance + f_traction + f_brake)
            traction_limited_list.append(t_limited)
            brake_limited_list.append(b_limited)
            electric_mag_list.append(electric_mag)
            friction_mag_list.append(friction_mag)
        return (forces, traction_limited_list, brake_limited_list,
                electric_mag_list, friction_mag_list)

    def _calc_all_coupler_forces(self, consist, states, abs_positions):
        """计算所有相邻车对之间的车钩力。

        abs_positions[i] 是第 i 节车在线路上的绝对里程。
        头车 (i=0) 在最前方，有最大的绝对位置值。
        """
        n = len(states)
        forces = []
        for i in range(n - 1):
            # car i 是前车（更前方，更大 offset），car i+1 是后车
            # Δx = 前车位置 - 后车位置 - 车长（前车长度）
            delta_x = abs_positions[i] - abs_positions[i + 1] - consist[i].length
            # Δv = v_rear - v_front（方案定义：后方减前方）
            delta_v = states[i + 1].velocity - states[i].velocity
            f = _calc_coupler_force_raw(delta_x, delta_v, self.coupler_config)
            forces.append(f)
        return forces

    @staticmethod
    def _calc_net_forces(external_forces, coupler_forces, n_cars):
        """聚合单车合力 = 外部力 + 车钩力贡献。"""
        net = list(external_forces)  # copy
        for i in range(n_cars):
            # 与前方车的车钩（car i 是后方车）：力作用在 car i 上 = +F
            if i > 0:
                net[i] += coupler_forces[i - 1]
            # 与后方车的车钩（car i 是前方车）：反作用力 = -F
            if i < n_cars - 1:
                net[i] -= coupler_forces[i]
        return net

