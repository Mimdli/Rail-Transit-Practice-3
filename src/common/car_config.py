"""CarConfig — 单车物理参数与车钩参数

阶段 2：定义单节车辆的物理参数和车钩参数，提供动车/拖车预设。
"""

from dataclasses import dataclass, field


# ═══════════════════════════════════════════════════════════════
# 车钩参数
# ═══════════════════════════════════════════════════════════════

@dataclass
class CouplerConfig:
    """车钩物理参数。

    Attributes:
        stiffness: 车钩刚度 K (N/m)，典型值 ~1e7。
        damping: 车钩阻尼 D (N·s/m)，典型值 ~1e5。
        slack: 车钩间隙半宽 (m)，即自由行程的一半。
        max_force: 车钩最大承受力 (N)，超出视为断钩。

    Note:
        K ≈ 10^7 N/m 是真实物理参数，不可调小。该量级使车钩力方程
        成为刚性方程（Stiff Equation），需要微步长解算器保证数值稳定性。
    """
    stiffness: float = 1e7       # N/m
    damping: float = 1e5         # N·s/m
    slack: float = 0.02          # m (间隙半宽)
    max_force: float = 4e5       # N (400 kN，运营极限；断钩阈值约 2000 kN)


# 默认车钩配置
DEFAULT_COUPLER_CONFIG = CouplerConfig(
    stiffness=1e7,     # N/m
    damping=1e5,       # N·s/m
    slack=0.02,        # m
    max_force=4e5,     # N (400 kN 运营极限)
)


# ═══════════════════════════════════════════════════════════════
# 单车参数
# ═══════════════════════════════════════════════════════════════

@dataclass
class CarConfig:
    """单节车辆的物理参数。

    Attributes:
        name: 车辆类型名称（如 "MotorCar", "TrailerCar"）。
        mass: 车辆质量 (kg)。AW2（定员载荷）为基准。
        length: 车体长度 (m)。
        is_motor: 是否为动车（有动力）。
        davis_A: Davis 阻力常数项 (N)。
        davis_B: Davis 阻力一次项系数 (N/(m/s))。
        davis_C: Davis 阻力二次项系数 (N/(m²/s²))。
        max_traction_force: 恒力矩区最大牵引力 (N)。
        traction_transition_speed: 恒力矩→恒功率转换速度 (m/s)。
        construction_speed: 构造速度 (m/s)，牵引力自然衰减至 0 的速度。
        max_service_brake_force: 最大常用制动力 (N)。
        max_emergency_brake_force: 最大紧急制动力 (N)。
        tunnel_resistance_factor: 隧道附加阻力系数。
    """
    name: str = ""
    mass: float = 60000.0                # kg (当前有效质量，运行时可变)
    length: float = 19.5                 # m (B型地铁标准车长)
    is_motor: bool = True

    # 回转质量系数：考虑旋转部件（轮对、电机转子）转动惯量
    # 等效质量 = 静质量 × rotary_mass_factor，典型范围 1.06~1.12
    rotary_mass_factor: float = 1.08

    # Davis 基本阻力参数: R = A + B*v + C*v²
    davis_A: float = 1200.0              # N
    davis_B: float = 30.0                # N/(m/s)
    davis_C: float = 3.0                 # N/(m²/s²)

    # 牵引特性
    max_traction_force: float = 80000.0            # N (恒力矩区)
    traction_transition_speed: float = 10.0         # m/s (36 km/h)
    construction_speed: float = 22.22               # m/s (80 km/h)

    # 制动特性
    max_service_brake_force: float = 80000.0        # N
    max_emergency_brake_force: float = 100000.0     # N

    # 隧道附加阻力系数（隧道内基本阻力增加的比例）
    tunnel_resistance_factor: float = 1.3

    # ── 载荷等级质量 ─────────────────────────────────────────
    # base_mass 为 AW2（定员）设计基准质量，用于制动负荷补偿。
    # aw0~aw3 为各载荷等级对应的车辆总质量 (kg)。
    base_mass: float = 60000.0           # kg (AW2 基准质量)
    aw0_mass: float = 38800.0            # kg (AW0 空载, B型地铁约 38.8t)
    aw1_mass: float = 47200.0            # kg (AW1 满座, +8.4t 乘客)
    aw2_mass: float = 60000.0            # kg (AW2 定员, +14.4t 乘客)
    aw3_mass: float = 67200.0            # kg (AW3 超载, +21.6t 乘客)


# ═══════════════════════════════════════════════════════════════
# 预设
# ═══════════════════════════════════════════════════════════════

MOTOR_CAR_CONFIG = CarConfig(
    name="MotorCar",
    mass=60000.0,         # 60t (AW2, 动车较重)
    length=19.5,
    is_motor=True,
    rotary_mass_factor=1.08,
    davis_A=1200.0,
    davis_B=30.0,
    davis_C=3.0,
    max_traction_force=80000.0,
    traction_transition_speed=10.0,
    construction_speed=22.22,
    max_service_brake_force=80000.0,
    max_emergency_brake_force=100000.0,
    tunnel_resistance_factor=1.3,
    base_mass=60000.0,
    aw0_mass=38800.0,
    aw1_mass=47200.0,
    aw2_mass=60000.0,
    aw3_mass=67200.0,
)

TRAILER_CAR_CONFIG = CarConfig(
    name="TrailerCar",
    mass=46000.0,         # 46t (AW2, 拖车较轻)
    length=19.5,
    is_motor=False,
    rotary_mass_factor=1.06,  # 拖车无电机转子，回转质量系数较低
    davis_A=900.0,
    davis_B=25.0,
    davis_C=2.5,
    max_traction_force=0.0,        # 拖车无动力
    traction_transition_speed=0.0,
    construction_speed=22.22,
    max_service_brake_force=65000.0,
    max_emergency_brake_force=80000.0,
    tunnel_resistance_factor=1.3,
    base_mass=46000.0,
    aw0_mass=30000.0,
    aw1_mass=36400.0,
    aw2_mass=46000.0,
    aw3_mass=51600.0,
)
