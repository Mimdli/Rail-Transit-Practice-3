"""单车力计算 — 纯函数集合

阶段 5：计算单节车受到的各种力。所有函数均为纯函数——输入参数，返回数值，
不持有状态，不访问全局变量。

力符号约定:
    正力 = 前进方向（加速），负力 = 后退方向（减速/制动）。

接口:
    IEnvironmentQuery — 车辆模块对环境数据的依赖（定义在此以保持内聚）。
"""

from abc import ABC, abstractmethod
import math
from src.common.car_config import CarConfig


# ═══════════════════════════════════════════════════════════════
# 环境接口
# ═══════════════════════════════════════════════════════════════

class IEnvironmentQuery(ABC):
    """车辆模块对环境数据的唯一依赖。"""

    @abstractmethod
    def get_adhesion_coefficient(self) -> float:
        """返回当前粘着系数 (干燥=0.18, 雨=0.10, 雪=0.06)。"""
        ...


# ═══════════════════════════════════════════════════════════════
# 力计算函数
# ═══════════════════════════════════════════════════════════════

def calc_davis_resistance(car: CarConfig, velocity: float) -> float:
    """计算 Davis 基本阻力 (N)。

    公式: R = A + B*v + C*v²
    阻力始终与运动方向相反（返回非正值）。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)，取绝对值用于计算。

    Returns:
        阻力 (N)，≤ 0。
    """
    v = abs(velocity)
    magnitude = car.davis_A + car.davis_B * v + car.davis_C * v * v
    # 阻力方向与速度方向相反
    if velocity >= 0:
        return -magnitude
    else:
        return magnitude


def calc_grade_resistance(car: CarConfig, gradient: float) -> float:
    """计算坡道阻力 (N)。

    公式: F = -m * g * gradient / 1000
    上坡 (gradient > 0) → 负力（阻碍前进）。
    下坡 (gradient < 0) → 正力（助力前进）。

    Args:
        car: 车辆配置。
        gradient: 坡度 (‰)，正值为上坡。

    Returns:
        坡道力 (N)。
    """
    g = 9.81
    return -car.mass * g * gradient / 1000.0


def calc_tunnel_resistance(car: CarConfig, velocity: float, is_tunnel: bool) -> float:
    """计算隧道附加阻力 (N)。

    隧道内阻力增加：额外阻力 = (factor - 1) * Davis阻力幅值。
    非隧道返回 0。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        is_tunnel: 是否在隧道内。

    Returns:
        隧道附加阻力 (N)，≤ 0（隧道内的额外阻碍）。
    """
    if not is_tunnel:
        return 0.0
    v = abs(velocity)
    davis_magnitude = car.davis_A + car.davis_B * v + car.davis_C * v * v
    extra = davis_magnitude * (car.tunnel_resistance_factor - 1.0)
    if velocity >= 0:
        return -extra
    else:
        return extra


def calc_total_resistance(car: CarConfig, velocity: float,
                          gradient: float, is_tunnel: bool) -> float:
    """计算总阻力 (N) = Davis基本阻力 + 坡道阻力 + 隧道附加阻力。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        gradient: 坡度 (‰)。
        is_tunnel: 是否在隧道内。

    Returns:
        总阻力 (N)，通常为负值（阻碍运动）。
    """
    return (calc_davis_resistance(car, velocity) +
            calc_grade_resistance(car, gradient) +
            calc_tunnel_resistance(car, velocity, is_tunnel))


def calc_tractive_force(car: CarConfig, velocity: float,
                        throttle: float, adhesion_coefficient: float = 0.18) -> float:
    """计算牵引力 (N)，含黏着限制。

    牵引特性曲线:
        v < transition_speed:  F = max_traction_force（恒力矩区）
        transition_speed ≤ v < construction_speed: F = P/v（恒功率区）
        v ≥ construction_speed: F = 0（自然衰减至零）

    黏着限制: |F| ≤ adhesion_coefficient * mass * g
    拖车无动力，始终返回 0。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)，取绝对值。
        throttle: 牵引手柄位 (0.0 ~ 1.0)。
        adhesion_coefficient: 轮轨黏着系数。

    Returns:
        牵引力 (N)，≥ 0。
    """
    if not car.is_motor or throttle <= 0.0:
        return 0.0

    v = abs(velocity)

    # 牵引特性曲线
    if v < car.traction_transition_speed:
        raw_force = car.max_traction_force
    elif v < car.construction_speed:
        # 恒功率: F = F_max * v_transition / v
        raw_force = car.max_traction_force * car.traction_transition_speed / v
    else:
        raw_force = 0.0

    force = raw_force * throttle

    # 黏着限制
    adhesion_limit = adhesion_coefficient * car.mass * 9.81
    if force > adhesion_limit:
        force = adhesion_limit

    return force


def calc_brake_force(car: CarConfig, velocity: float,
                     brake_level: float, adhesion_coefficient: float = 0.18) -> float:
    """计算制动力 (N)，含黏着限制。

    制动力与运动方向相反（返回非正值）。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        brake_level: 制动级别 (0.0 ~ 1.0，1.0 为紧急制动)。
        adhesion_coefficient: 轮轨黏着系数。

    Returns:
        制动力 (N)，≤ 0。
    """
    if brake_level <= 0.0 or abs(velocity) < 1e-9:
        return 0.0

    # 线性插值: brake_level 0→1 映射到 常用制动 → 紧急制动
    max_brake = (car.max_service_brake_force +
                 (car.max_emergency_brake_force - car.max_service_brake_force) * brake_level)

    magnitude = max_brake * brake_level

    # 黏着限制
    adhesion_limit = adhesion_coefficient * car.mass * 9.81
    if magnitude > adhesion_limit:
        magnitude = adhesion_limit

    # 制动力方向与速度相反
    if velocity >= 0:
        return -magnitude
    else:
        return magnitude
