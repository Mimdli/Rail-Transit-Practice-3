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


class StationPhase(Enum):
    """停站阶段 — AutoDriveController 状态机"""
    CRUISING = auto()     # 巡航/行驶中
    APPROACHING = auto()  # 制动接近车站
    DWELL = auto()        # 停站中（门开，等待乘客乘降）
    DEPARTING = auto()    # 准备发车（门已关，等待发车条件）


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
# 车辆运行状态
# ═══════════════════════════════════════════════════════════════

class VehicleState(Enum):
    """车辆运行状态 — 状态机驱动

    状态转换规则:
        INIT → STOPPED          reset_states() / startup() 完成
        STOPPED → STARTING      start_moving() 命令（需满足联锁）
        STARTING → MOVING       速度 > 0.01 m/s
        MOVING → BRAKING        施加制动（brake > 0 且速度在下降）
        BRAKING → STOPPED       is_stopped（所有车厢速度 < 0.01 m/s）
        MOVING → COASTING       throttle=0 且 brake=0 且 速度 > 0
        COASTING → MOVING       重新施加牵引
        COASTING → BRAKING      施加制动
        任意状态 → EMERGENCY    emergency_brake() 命令
        EMERGENCY → STOPPED     is_stopped + 确认
    """
    INIT = auto()          # 刚初始化，尚未就绪
    STOPPED = auto()       # 停止中（所有车静止，保持制动）
    STARTING = auto()      # 启动中（已释放制动，正在施加牵引）
    MOVING = auto()        # 运行中（正在加速或匀速）
    COASTING = auto()      # 惰行中（无牵引无制动）
    BRAKING = auto()       # 制动中（正在减速）
    EMERGENCY = auto()     # 紧急制动


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
