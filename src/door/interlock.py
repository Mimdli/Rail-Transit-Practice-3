"""车门与站台联锁模块 — 保证停车、开门、发车符合安全规则"""

from src.vehicle.model import VehicleModel, DoorSide
from src.track.data import TrackData


class DoorInterlock:
    """车门联锁控制器"""

    def __init__(self, vehicle: VehicleModel, track: TrackData):
        self.vehicle = vehicle
        self.track = track

    def can_open_door(self) -> tuple:
        """检查是否允许开门，返回 (允许, 原因)"""
        if self.vehicle.speed > 0.1:
            return False, "列车未停稳，不能开门"
        return True, ""

    def get_allowed_door_side(self) -> DoorSide:
        """获取到站后允许开门的侧"""
        pos = self.vehicle.position
        side = self.track.get_platform_side_at(pos)
        if side == "left":
            return DoorSide.LEFT
        elif side == "right":
            return DoorSide.RIGHT
        return DoorSide.NONE

    def can_depart(self) -> tuple:
        """检查是否允许发车，返回 (允许, 原因)"""
        if not self.vehicle.doors_closed():
            return False, "车门未关闭，不能发车"
        return True, ""

    def is_at_platform(self) -> bool:
        """判断列车是否在站台范围内"""
        pos = self.vehicle.position
        for p in self.track.platforms:
            platform_length = getattr(p, "length", 120.0)
            if abs(pos - p.position) < platform_length / 2:
                return True
        return False
