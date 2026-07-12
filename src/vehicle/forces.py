"""单车力计算 — 纯函数集合

阶段 5：计算单节车受到的各种力。所有函数均为纯函数——输入参数，返回数值，
不持有状态，不访问全局变量。

力符号约定:
    正力 = 前进方向（加速），负力 = 后退方向（减速/制动）。

接口:
    IEnvironmentQuery — 车辆模块对环境数据的依赖（定义在此以保持内聚）。
"""

import math
from typing import Optional
from src.common.car_config import CarConfig

# 环境接口从 environment.py 导入，在此 re-export 以保持向后兼容
from src.vehicle.environment import IEnvironmentQuery  # noqa: F401


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
                          gradient: float, is_tunnel: bool,
                          curve_radius: Optional[float] = None) -> float:
    """计算总阻力 (N) = Davis基本阻力 + 坡道阻力 + 隧道附加阻力 + 曲线附加阻力。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        gradient: 坡度 (‰)。
        is_tunnel: 是否在隧道内。
        curve_radius: 曲线半径 (m)，None 表示直线。

    Returns:
        总阻力 (N)，通常为负值（阻碍运动）。
    """
    return (calc_davis_resistance(car, velocity) +
            calc_grade_resistance(car, gradient) +
            calc_tunnel_resistance(car, velocity, is_tunnel) +
            calc_curve_resistance(car, curve_radius))


def calc_curve_resistance(car: CarConfig, radius: Optional[float]) -> float:
    """计算曲线附加阻力 (N)。

    经验公式: R_curve ≈ 700 / R × m × g
    其中 R 为曲线半径 (m)。

    阻力始终与运动方向相反（返回非正值）。

    Args:
        car: 车辆配置。
        radius: 曲线半径 (m)，None 或 ≤ 0 表示直线（返回 0）。

    Returns:
        曲线附加阻力 (N)，≤ 0。
    """
    if radius is None or radius <= 0:
        return 0.0
    g = 9.81
    magnitude = 700.0 / radius * car.mass * g
    return -magnitude


def calc_tractive_force(car: CarConfig, velocity: float,
                        throttle: float, adhesion_coefficient: float = 0.18
                        ) -> tuple:
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
        (force, traction_limited) — 牵引力 (N, ≥ 0) 和是否被黏着限制截断。
    """
    if not car.is_motor or throttle <= 0.0:
        return 0.0, False

    v = abs(velocity)

    # 牵引特性曲线
    if v < car.traction_transition_speed:
        raw_force = car.max_traction_force
    elif v < car.construction_speed:
        # 恒功率: F = F_max * v_transition / v
        raw_force = car.max_traction_force * car.traction_transition_speed / v
    else:
        # 自然特性区: 牵引力平滑衰减至零，避免构造速度处的硬截断导数速度振荡
        # F(v) = F_c * (v_c / v)^2，在 v=v_c 处与恒功率区连续
        vc = car.construction_speed
        fc = car.max_traction_force * car.traction_transition_speed / vc
        raw_force = fc * (vc / v) ** 2

    force = raw_force * throttle

    # 黏着限制
    adhesion_limit = adhesion_coefficient * car.mass * 9.81
    traction_limited = force > adhesion_limit
    if traction_limited:
        force = adhesion_limit

    return force, traction_limited


def _calc_raw_brake_magnitude(car: CarConfig, brake_level: float) -> float:
    """计算制动需求幅值（不含方向、黏着限制），供电空拆分复用。

    包含: 常用→紧急插值 + 载荷补偿。
    """
    max_brake = (car.max_service_brake_force +
                 (car.max_emergency_brake_force - car.max_service_brake_force) * brake_level)
    magnitude = max_brake
    # 载荷补偿：重载列车需要更大的制动力
    if hasattr(car, 'base_mass') and car.base_mass > 0:
        load_compensation = car.mass / car.base_mass
        magnitude *= load_compensation
    return magnitude


def calc_electric_brake_magnitude(car: CarConfig, velocity: float,
                                    brake_level: float) -> float:
    """计算电气制动（再生制动）需求幅值 (N, ≥ 0)。

    动车承担 70% 总制动需求，支持全速度范围完全电制动（含 0 km/h 停车）。
    拖车无电气制动能力。

    注意: 此函数返回原始需求值，不含黏着限制。
    黏着限制应由调用方对总制动力（电气+空气）统一施加。

    速度参数保留用于未来再生效率 η(v) 的独立计算（低速反电动势不足时
    回收效率趋零，但电机制动转矩仍可维持），此函数不处理能量回收。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)，取绝对值。
        brake_level: 制动级别 (0.0 ~ 1.0)。

    Returns:
        电气制动需求幅值 (N, ≥ 0)。
    """
    if not car.is_motor or brake_level <= 0.0:
        return 0.0

    total_magnitude = _calc_raw_brake_magnitude(car, brake_level)
    electric_magnitude = total_magnitude * 0.7

    return electric_magnitude


def calc_friction_brake_magnitude(car: CarConfig, brake_level: float,
                                    electric_magnitude: float) -> float:
    """计算空气制动（摩擦制动）需求幅值 (N, ≥ 0)。

    补充电气制动不足的差额（如拖车、过载等情景），维持总制动力 = 需求值。
    electric_magnitude 为 calc_electric_brake_magnitude 的返回值。

    Args:
        car: 车辆配置。
        brake_level: 制动级别 (0.0 ~ 1.0)。
        electric_magnitude: 电气制动需求幅值 (N)。

    Returns:
        空气制动需求幅值 (N, ≥ 0)。
    """
    if brake_level <= 0.0:
        return 0.0

    total_magnitude = _calc_raw_brake_magnitude(car, brake_level)
    return max(0.0, total_magnitude - electric_magnitude)


def calc_electric_brake_force(car: CarConfig, velocity: float,
                               brake_level: float) -> float:
    """计算电气制动（再生制动）力 (N)，含符号。

    正值 = 前进方向。速度方向决定符号（制动力与运动方向相反）。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        brake_level: 制动级别 (0.0 ~ 1.0)。

    Returns:
        电气制动力 (N)，与运动方向相反的带符号力。
    """
    mag = calc_electric_brake_magnitude(car, velocity, brake_level)
    if velocity >= 0:
        return -mag
    else:
        return mag


def calc_friction_brake_force(car: CarConfig, velocity: float,
                               brake_level: float,
                               electric_magnitude: float) -> float:
    """计算空气制动（摩擦制动）力 (N)，含符号。

    正值 = 前进方向。补充电气制动不足的差额（如拖车、过载等情景）。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        brake_level: 制动级别 (0.0 ~ 1.0)。
        electric_magnitude: 电气制动需求幅值 (N)，用于补偿计算。

    Returns:
        空气制动力 (N)，与运动方向相反的带符号力。
    """
    mag = calc_friction_brake_magnitude(car, brake_level, electric_magnitude)
    if velocity >= 0:
        return -mag
    else:
        return mag


def calc_brake_force(car: CarConfig, velocity: float,
                     brake_level: float, adhesion_coefficient: float = 0.18) -> tuple:
    """计算总制动力 (N) = 电气制动 + 空气制动，含黏着限制和载荷补偿。

    电气制动与空气制动协同工作，维持总需求不变。
    黏着限制对总制动力统一施加（单轴黏着不能区分电/空）。

    Args:
        car: 车辆配置。
        velocity: 当前速度 (m/s)。
        brake_level: 制动级别 (0.0 ~ 1.0，1.0 为紧急制动)。
        adhesion_coefficient: 轮轨黏着系数。

    Returns:
        (total_force, brake_limited) — 总制动力 (N, ≤ 0) 和是否被黏着限制截断。
    """
    if brake_level <= 0.0 or abs(velocity) < 1e-9:
        return 0.0, False

    # 电空需求拆分
    electric_mag = calc_electric_brake_magnitude(car, velocity, brake_level)
    friction_mag = calc_friction_brake_magnitude(car, brake_level, electric_mag)
    total_magnitude = electric_mag + friction_mag

    # 黏着限制（对总制动力统一施加）
    adhesion_limit = adhesion_coefficient * car.mass * 9.81
    brake_limited = total_magnitude > adhesion_limit
    if brake_limited:
        total_magnitude = adhesion_limit

    # 制动力方向与速度相反
    if velocity >= 0:
        return -total_magnitude, brake_limited
    else:
        return total_magnitude, brake_limited
