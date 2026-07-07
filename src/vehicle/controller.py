"""车辆控制器 — TODO: 实现手动驾驶与自动驾驶"""

from src.vehicle.model import VehicleModel


class ManualController:
    """手动驾驶控制器"""

    def __init__(self, vehicle: VehicleModel):
        self.vehicle = vehicle

    def set_traction(self):
        """设置牵引"""
        raise NotImplementedError

    def set_coast(self):
        """设置惰行"""
        raise NotImplementedError

    def set_service_brake(self):
        """设置常用制动"""
        raise NotImplementedError

    def set_full_brake(self):
        """设置全制动"""
        raise NotImplementedError

    def set_emergency_brake(self):
        """设置紧急制动"""
        raise NotImplementedError

    def open_left_door(self):
        """打开左侧门"""
        raise NotImplementedError

    def open_right_door(self):
        """打开右侧门"""
        raise NotImplementedError

    def close_door(self):
        """关闭车门"""
        raise NotImplementedError


class AutoController:
    """自动驾驶控制器"""

    def __init__(self, vehicle: VehicleModel):
        self.vehicle = vehicle
        self.target_position: float = 0.0

    def set_target(self, position: float):
        """设置目标停车位置"""
        raise NotImplementedError

    def step(self):
        """自动控制步进"""
        raise NotImplementedError

    def is_stopped(self) -> bool:
        """判断是否已停车"""
        raise NotImplementedError
