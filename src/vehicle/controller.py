"""车辆控制器 — 手动驾驶与自动驾驶"""

from src.vehicle.model import VehicleModel, ControlLevel, RunningMode


class ManualController:
    """手动驾驶控制器"""

    def __init__(self, vehicle: VehicleModel):
        self.vehicle = vehicle

    def set_traction(self):
        """设置牵引"""
        self.vehicle.apply_traction(ControlLevel.FULL_TRACTION)

    def set_coast(self):
        """设置惰行"""
        self.vehicle.apply_traction(ControlLevel.COAST)

    def set_service_brake(self):
        """设置常用制动"""
        self.vehicle.apply_traction(ControlLevel.SERVICE_BRAKE)

    def set_full_brake(self):
        """设置全制动"""
        self.vehicle.apply_traction(ControlLevel.FULL_BRAKE)

    def set_emergency_brake(self):
        """设置紧急制动"""
        self.vehicle.apply_traction(ControlLevel.EMERGENCY_BRAKE)

    def open_left_door(self):
        """打开左侧门"""
        from src.vehicle.model import DoorSide
        return self.vehicle.open_door(DoorSide.LEFT)

    def open_right_door(self):
        """打开右侧门"""
        from src.vehicle.model import DoorSide
        return self.vehicle.open_door(DoorSide.RIGHT)

    def close_door(self):
        """关闭车门"""
        self.vehicle.close_door()


class AutoController:
    """自动驾驶控制器"""

    def __init__(self, vehicle: VehicleModel):
        self.vehicle = vehicle
        self.target_position: float = 0.0       # 目标停车位置 (m)
        self.approach_speed: float = 5.0        # 接近速度 (m/s)
        self.stop_distance: float = 20.0        # 开始精确停车的距离 (m)

    def set_target(self, position: float):
        """设置目标停车位置"""
        self.target_position = position

    def step(self):
        """自动控制步进"""
        distance = self.target_position - self.vehicle.position

        if distance <= 0.5:
            self.vehicle.set_control_level_direct(ControlLevel.EMERGENCY_BRAKE)
        elif distance < self.stop_distance:
            target_speed = self.approach_speed * (distance / self.stop_distance)
            if self.vehicle.speed > target_speed + 0.5:
                self.vehicle.set_control_level_direct(ControlLevel.SERVICE_BRAKE)
            elif self.vehicle.speed < target_speed - 0.5:
                self.vehicle.set_control_level_direct(ControlLevel.LOW_TRACTION)
            else:
                self.vehicle.set_control_level_direct(ControlLevel.COAST)
        else:
            if self.vehicle.speed < self.vehicle.current_speed_limit * 0.9:
                self.vehicle.set_control_level_direct(ControlLevel.MEDIUM_TRACTION)
            else:
                self.vehicle.set_control_level_direct(ControlLevel.COAST)

    def is_stopped(self) -> bool:
        """判断是否已停车"""
        return self.vehicle.speed < 0.01
