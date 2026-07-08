"""车辆动力学模型 — 列车运动状态模拟（旧单质点模型）

⚠ 已废弃: 此模块为旧单质点动力学模型，保留仅为 UI 层兼容。
   新代码请使用:
       - VehicleController (src.vehicle.vehicle_controller)
       - PerCarDynamicsPipeline (src.vehicle.dynamics_pipeline)
       - AutoDriveController (src.vehicle.auto_drive)
   枚举请直接从 src.vehicle.enums 导入。
"""

import warnings
from enum import auto

# 枚举统一从 enums.py re-export（保持旧 import 路径兼容）
from src.vehicle.enums import ControlLevel, DoorSide, RunningMode  # noqa: F401


class VehicleModel:
    """车辆动力学模型（旧单质点模型）。

    ⚠ 已废弃: 此类为旧单质点动力学模型的遗留代码，仅由 UI 层
    (src/ui/*) 临时引用。迁移完成后将被移除。

    新代码请使用 VehicleController + PerCarDynamicsPipeline。
    """

    def __init__(self):
        warnings.warn(
            "VehicleModel is deprecated. Use VehicleController instead.",
            DeprecationWarning, stacklevel=2,
        )
        # 运动状态
        self.position: float = 0.0          # 当前位置 (m)
        self.speed: float = 0.0             # 当前速度 (m/s)
        self.acceleration: float = 0.0      # 当前加速度 (m/s²)

        # 车辆参数
        self.mass: float = 50000.0          # 车辆质量 (kg)
        self.max_speed: float = 22.0        # 最高速度 (m/s) ≈ 80 km/h
        self.max_traction: float = 1.2      # 最大牵引加速度 (m/s²)
        self.max_service_brake: float = -1.0  # 常用制动减速度 (m/s²)
        self.max_emergency_brake: float = -1.5  # 紧急制动减速度 (m/s²)

        # 控制状态
        self.control_level: ControlLevel = ControlLevel.COAST
        self.running_mode: RunningMode = RunningMode.MANUAL

        # 车门状态
        self.left_door_open: bool = False
        self.right_door_open: bool = False
        self.door_side: DoorSide = DoorSide.NONE

        # 线路条件（由外部更新）
        self.current_gradient: float = 0.0       # 当前坡度 (‰)
        self.current_speed_limit: float = 22.0   # 当前限速 (m/s)

        # 时间
        self.dt: float = 0.1                     # 仿真步长 (s)

    def apply_traction(self, level: ControlLevel):
        """设置牵引/制动级位"""
        if self.running_mode != RunningMode.MANUAL:
            return
        self.control_level = level

    def set_control_level_direct(self, level: ControlLevel):
        """直接设置控制级位（自动模式使用）"""
        self.control_level = level

    def open_door(self, side: DoorSide):
        """开门"""
        if self.speed > 0.1:
            return False
        if side == DoorSide.LEFT:
            self.left_door_open = True
        elif side == DoorSide.RIGHT:
            self.right_door_open = True
        self.door_side = side
        return True

    def close_door(self):
        """关门"""
        self.left_door_open = False
        self.right_door_open = False
        self.door_side = DoorSide.NONE

    def doors_closed(self) -> bool:
        """检查所有车门是否关闭"""
        return not self.left_door_open and not self.right_door_open

    def _calc_traction_force(self) -> float:
        """根据控制级位计算牵引力产生的加速度"""
        if self.control_level == ControlLevel.EMERGENCY_BRAKE:
            return self.max_emergency_brake
        elif self.control_level == ControlLevel.FULL_BRAKE:
            return self.max_service_brake
        elif self.control_level == ControlLevel.SERVICE_BRAKE:
            return self.max_service_brake * 0.6
        elif self.control_level == ControlLevel.COAST:
            return 0.0
        elif self.control_level == ControlLevel.LOW_TRACTION:
            return self.max_traction * 0.3
        elif self.control_level == ControlLevel.MEDIUM_TRACTION:
            return self.max_traction * 0.6
        elif self.control_level == ControlLevel.FULL_TRACTION:
            return self.max_traction
        return 0.0

    def _calc_resistance(self) -> float:
        """计算基本阻力 (m/s²)"""
        # 简化的阻力公式：R = a + b*v + c*v²
        a, b, c = 0.002, 0.0003, 0.00002
        v = self.speed
        return -(a + b * v + c * v * v)

    def _calc_gradient_force(self) -> float:
        """计算坡度产生的加速度分量 (m/s²)"""
        g = 9.81
        return -g * (self.current_gradient / 1000.0)

    def step(self):
        """推进一个仿真步长"""
        # 计算合力加速度
        traction = self._calc_traction_force()
        resistance = self._calc_resistance()
        gradient = self._calc_gradient_force()
        self.acceleration = traction + resistance + gradient

        # 更新速度
        new_speed = self.speed + self.acceleration * self.dt
        if new_speed < 0:
            new_speed = 0.0

        # 限速约束
        if new_speed > self.current_speed_limit:
            new_speed = self.current_speed_limit
        if new_speed > self.max_speed:
            new_speed = self.max_speed

        self.speed = new_speed

        # 更新位置
        self.position += self.speed * self.dt

    def get_speed_kmh(self) -> float:
        """获取速度 (km/h)"""
        return self.speed * 3.6

    def reset(self):
        """重置车辆状态"""
        self.position = 0.0
        self.speed = 0.0
        self.acceleration = 0.0
        self.control_level = ControlLevel.COAST
        self.close_door()
