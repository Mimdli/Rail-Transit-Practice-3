"""车辆仿真模块共享枚举

包含车门侧、运行模式、司控器级位及其到连续 throttle/brake 的映射。

注意：此文件独立于旧模块 model.py 中的同名枚举，两者不共享引用。
旧系统后续可重构为从此文件 re-export。
"""

from enum import Enum, auto


# ═══════════════════════════════════════════════════════════════
# 车门侧
# ═══════════════════════════════════════════════════════════════

class DoorSide(Enum):
    """车门侧"""
    NONE = 0
    LEFT = 1
    RIGHT = 2


# ═══════════════════════════════════════════════════════════════
# 运行模式
# ═══════════════════════════════════════════════════════════════

class RunningMode(Enum):
    """运行模式"""
    MANUAL = auto()      # 手动驾驶
    AUTOMATIC = auto()   # 自动驾驶（ATO）


# ═══════════════════════════════════════════════════════════════
# 司控器级位
# ═══════════════════════════════════════════════════════════════

class ControlLevel(Enum):
    """司控器级位（7 级离散手柄位置）"""
    EMERGENCY_BRAKE = -3
    FULL_BRAKE = -2
    SERVICE_BRAKE = -1
    COAST = 0
    LOW_TRACTION = 1
    MEDIUM_TRACTION = 2
    FULL_TRACTION = 3


# ═══════════════════════════════════════════════════════════════
# 载荷等级
# ═══════════════════════════════════════════════════════════════

class LoadLevel(Enum):
    """列车载荷等级（AW = Axle Weight 轴重）"""
    AW0 = 0   # 空载（空车，无乘客）
    AW1 = 1   # 满座（全部座位坐满）
    AW2 = 2   # 定员（额定载客，设计基准）
    AW3 = 3   # 超载（最大载客）


# ═══════════════════════════════════════════════════════════════
# ControlLevel → (throttle, brake_level) 映射
# ═══════════════════════════════════════════════════════════════
#
# brake_level 在 calc_brake_force() 中用于线性插值 service↔emergency，
# 并经载荷补偿和黏着限制后输出最终制动力。
# 以下映射值是针对默认 CarConfig 参数的近似标定。
# 如果 CarConfig 制动参数变更，此映射需重新标定。

CONTROL_LEVEL_MAP = {
    ControlLevel.EMERGENCY_BRAKE:  (0.0, 1.0),
    ControlLevel.FULL_BRAKE:       (0.0, 0.7),
    ControlLevel.SERVICE_BRAKE:    (0.0, 0.4),
    ControlLevel.COAST:            (0.0, 0.0),
    ControlLevel.LOW_TRACTION:     (0.3, 0.0),
    ControlLevel.MEDIUM_TRACTION:  (0.6, 0.0),
    ControlLevel.FULL_TRACTION:    (1.0, 0.0),
}
