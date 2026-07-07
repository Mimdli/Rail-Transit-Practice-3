"""车辆动力学模型 — TODO: 实现列车运动状态模拟"""

from enum import Enum, auto


class ControlLevel(Enum):
    """司控器级位"""
    EMERGENCY_BRAKE = -3
    FULL_BRAKE = -2
    SERVICE_BRAKE = -1
    COAST = 0
    LOW_TRACTION = 1
    MEDIUM_TRACTION = 2
    FULL_TRACTION = 3


class DoorSide(Enum):
    """车门侧"""
    NONE = 0
    LEFT = 1
    RIGHT = 2


class RunningMode(Enum):
    """运行模式"""
    MANUAL = auto()
    AUTOMATIC = auto()


class VehicleModel:
    """车辆动力学模型"""

    def __init__(self):
        # 运动状态
        self.position: float = 0.0
        self.speed: float = 0.0
        self.acceleration: float = 0.0

        # 车辆参数（默认值，后续可调）
        self.mass: float = 50000.0
        self.max_speed: float = 22.0
        self.max_traction: float = 1.2
        self.max_service_brake: float = -1.0
        self.max_emergency_brake: float = -1.5

        # 控制状态
        self.control_level: ControlLevel = ControlLevel.COAST
        self.running_mode: RunningMode = RunningMode.MANUAL

        # 车门状态
        self.left_door_open: bool = False
        self.right_door_open: bool = False
        self.door_side: DoorSide = DoorSide.NONE

        # 线路条件（由外部更新）
        self.current_gradient: float = 0.0
        self.current_speed_limit: float = 22.0

        # 仿真步长
        self.dt: float = 0.1

    def apply_traction(self, level: ControlLevel):
        """设置牵引/制动级位"""
        raise NotImplementedError

    def set_control_level_direct(self, level: ControlLevel):
        """直接设置控制级位（自动模式使用）"""
        raise NotImplementedError

    def open_door(self, side: DoorSide) -> bool:
        """开门"""
        raise NotImplementedError

    def close_door(self):
        """关门"""
        raise NotImplementedError

    def doors_closed(self) -> bool:
        """检查所有车门是否关闭"""
        raise NotImplementedError

    def step(self):
        """推进一个仿真步长"""
        raise NotImplementedError

    def get_speed_kmh(self) -> float:
        """获取速度 (km/h)"""
        raise NotImplementedError

    def reset(self):
        """重置车辆状态"""
        raise NotImplementedError
