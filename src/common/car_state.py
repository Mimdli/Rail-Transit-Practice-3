"""CarState — 单车状态快照

阶段 3：单节车在某一时刻的运动状态。
"""

from dataclasses import dataclass
from src.common.track_position import TrackPosition


@dataclass
class CarState:
    """单节车在某一时刻的运动状态快照。

    Attributes:
        position: 线路位置坐标。
        velocity: 当前速度 (m/s)，非负。
        acceleration: 当前加速度 (m/s²)。
    """
    position: TrackPosition
    velocity: float = 0.0
    acceleration: float = 0.0

    def copy(self) -> "CarState":
        """深拷贝当前状态。"""
        return CarState(
            position=TrackPosition(
                segment_id=self.position.segment_id,
                offset=self.position.offset,
            ),
            velocity=self.velocity,
            acceleration=self.acceleration,
        )
