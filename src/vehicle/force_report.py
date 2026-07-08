"""ForceReport — 力学报告数据结构

阶段 8：每个仿真步的输出数据结构，记录每节车受到的各类力分量。
"""

from dataclasses import dataclass, field
from typing import List
from src.common.track_position import TrackPosition


@dataclass
class CarForceReport:
    """单节车的力分量报告。

    所有力分量单位为牛顿 (N)。
    正力 = 前进方向（加速），负力 = 后退方向（减速/制动）。
    """
    car_index: int = 0                        # 车辆在编组中的序号 (0-based)
    position: TrackPosition = field(default_factory=lambda: TrackPosition(1, 0.0))
    velocity: float = 0.0                     # 当前速度 (m/s)
    acceleration: float = 0.0                 # 当前加速度 (m/s²)

    # 外力分量
    davis_resistance: float = 0.0             # Davis 基本阻力
    grade_resistance: float = 0.0             # 坡道阻力
    tunnel_resistance: float = 0.0            # 隧道附加阻力
    curve_resistance: float = 0.0             # 曲线附加阻力
    tractive_force: float = 0.0               # 牵引力
    brake_force: float = 0.0                  # 总制动力（电气+空气）
    electric_brake_force: float = 0.0         # 电气制动（再生制动）
    friction_brake_force: float = 0.0         # 空气制动（摩擦制动）

    # 黏着状态
    traction_limited: bool = False            # 牵引力是否被黏着限制截断（空转风险）
    brake_limited: bool = False               # 制动力是否被黏着限制截断（滑行风险）

    # 车钩力
    coupler_force_front: float = 0.0          # 前车钩力（与前方车的车钩力，正值=拉伸）
    coupler_force_rear: float = 0.0           # 后车钩力（与后方车的车钩力，正值=拉伸）
    net_coupler_force: float = 0.0            # 净车钩力（= 前车钩力对自身的贡献 + 后车钩力对自身的贡献）

    # 合力
    total_external_force: float = 0.0         # 总外力（阻力+牵引+制动）
    net_force: float = 0.0                    # 总合力 = 总外力 + 净车钩力


@dataclass
class ForceReport:
    """整列车在一个仿真步的完整力学报告。

    汇总所有 CarForceReport，附时间戳和仿真步序号。
    """
    step: int = 0                             # 仿真步序号
    timestamp: float = 0.0                    # 仿真时间 (s)
    dt: float = 0.0                           # 外部步长 (s)
    n_substeps: int = 0                       # 微步数量
    cars: List[CarForceReport] = field(default_factory=list)

    @property
    def head_position(self) -> TrackPosition:
        """头车位置。"""
        if self.cars:
            return self.cars[0].position
        return TrackPosition(1, 0.0)

    @property
    def head_velocity(self) -> float:
        """头车速度 (m/s)。"""
        if self.cars:
            return self.cars[0].velocity
        return 0.0

    @property
    def max_coupler_force(self) -> float:
        """整列车中的最大车钩力幅值 (N)。"""
        if not self.cars:
            return 0.0
        return max(
            max(abs(c.coupler_force_front), abs(c.coupler_force_rear))
            for c in self.cars
        )

    @property
    def total_tractive_force(self) -> float:
        """整列车总牵引力 (N)。"""
        return sum(c.tractive_force for c in self.cars)
