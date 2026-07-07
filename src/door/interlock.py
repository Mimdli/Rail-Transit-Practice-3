"""车门与站台联锁模块 — TODO: 实现安全联锁逻辑"""

from src.vehicle.model import VehicleModel, DoorSide
from src.track.data import TrackData


class DoorInterlock:
    """车门联锁控制器"""

    def __init__(self, vehicle: VehicleModel, track: TrackData):
        self.vehicle = vehicle
        self.track = track

    def can_open_door(self):
        """检查是否允许开门"""
        raise NotImplementedError

    def get_allowed_door_side(self) -> DoorSide:
        """获取允许开门的侧"""
        raise NotImplementedError

    def can_depart(self):
        """检查是否允许发车"""
        raise NotImplementedError

    def is_at_platform(self) -> bool:
        """判断是否在站台范围内"""
        raise NotImplementedError
