"""车门与站台联锁模块 — 保证停车、开门、发车符合安全规则

支持新旧两种车辆控制器:
    - VehicleController (新多体动力学)
    - VehicleModel (旧单质点，已废弃)
"""

from typing import Optional, Union
from src.vehicle.model import VehicleModel, DoorSide
from src.vehicle.vehicle_controller import VehicleController
from src.common.track_position import ITrackQuery, TrackPosition
from src.track.data import TrackData
from src.track.vision_alignment import VisionCoordinateMapper


class DoorInterlock:
    """车门联锁控制器。

    兼容 VehicleController（新版）和 VehicleModel（旧版），
    通过 _get_speed / _get_position / _doors_closed 统一访问。
    """

    def __init__(self, vehicle: Union[VehicleModel, VehicleController],
                 track: TrackData,
                 track_adapter: ITrackQuery = None,
                 vision_mapper: Optional[VisionCoordinateMapper] = None):
        self.vehicle = vehicle
        self.track = track
        self.track_adapter = track_adapter
        link_source = "demo" if len(track.segments) <= 10 else "directions"
        self.vision_mapper = vision_mapper or VisionCoordinateMapper(
            track, link_source)

    # ── 车辆状态访问（兼容新旧模型）──────────────────────────

    def _get_speed(self) -> float:
        """获取当前速度 (m/s)。"""
        v = self.vehicle
        if isinstance(v, VehicleController):
            return v.head_speed
        return v.speed

    def _get_position_abs(self) -> float:
        """获取头车在线路上的绝对位置 (m)。"""
        v = self.vehicle
        if isinstance(v, VehicleController):
            states = v.states
            if not states:
                return 0.0
            if self.track_adapter is not None:
                return self.track_adapter.to_absolute(states[0].position)
            return states[0].position.offset
        return v.position

    def _doors_closed(self) -> bool:
        v = self.vehicle
        if isinstance(v, VehicleController):
            return v.doors_closed()
        return v.doors_closed()

    def _get_track_position(self) -> Optional[TrackPosition]:
        """新版控制器可提供无歧义的 Seg 内位置。"""
        if isinstance(self.vehicle, VehicleController) and self.vehicle.states:
            return self.vehicle.states[0].position
        return None

    # ── 联锁逻辑 ────────────────────────────────────────────

    def can_open_door(self) -> tuple:
        """检查是否允许开门，返回 (允许, 原因)"""
        if self._get_speed() > 0.1:
            return False, "列车未停稳，不能开门"
        return True, ""

    def get_allowed_door_side(self) -> DoorSide:
        """获取到站后允许开门的侧"""
        pos = self._get_position_abs()
        track_position = self._get_track_position()
        side = ""
        if track_position is not None:
            side = self.vision_mapper.platform_side_at(
                track_position, self.vehicle.direction)
        if not side:
            segment_id = 0 if track_position is None else track_position.segment_id
            side = self.track.get_platform_side_at(pos, segment_id)
        if side == "left":
            return DoorSide.LEFT
        elif side == "right":
            return DoorSide.RIGHT
        return DoorSide.NONE

    def can_depart(self) -> tuple:
        """检查是否允许发车，返回 (允许, 原因)"""
        if not self._doors_closed():
            return False, "车门未关闭，不能发车"
        return True, ""

    def is_at_platform(self) -> bool:
        """判断列车是否在站台范围内"""
        pos = self._get_position_abs()
        track_position = self._get_track_position()
        if track_position is not None and self.vision_mapper.platform_at(
                track_position, self.vehicle.direction) is not None:
            return True
        segment_id = 0 if track_position is None else track_position.segment_id
        return self.track.get_platform_at(pos, segment_id) is not None
