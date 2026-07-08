"""TrackPosition — 一维线路位置坐标

阶段 1：表示列车在线路上的位置，支持沿线路推进。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class TrackPosition:
    """一维线路位置坐标。

    Attributes:
        segment_id: 当前所在区段编号（从 1 开始）。
        offset: 距该区段起点的距离（m），范围 [0, segment_length]。
    """
    segment_id: int
    offset: float


class ITrackQuery(ABC):
    """车辆模块对线路数据的唯一依赖。

    所有外部依赖通过此接口访问，开发期间使用 Mock 实现。
    """

    @abstractmethod
    def get_speed_limit(self, pos: TrackPosition) -> float:
        """返回当前位置的限速 (m/s)。"""
        ...

    @abstractmethod
    def get_gradient(self, pos: TrackPosition) -> float:
        """返回当前位置的坡度 (‰)。"""
        ...

    @abstractmethod
    def get_is_tunnel(self, pos: TrackPosition) -> bool:
        """返回当前位置是否在隧道内。"""
        ...

    @abstractmethod
    def get_curve_radius(self, pos: TrackPosition) -> Optional[float]:
        """返回当前位置的曲线半径 (m)。

        None 表示直线（无曲线附加阻力）。
        """
        ...

    @abstractmethod
    def advance_position(self, pos: TrackPosition, distance: float) -> TrackPosition:
        """沿线路推进指定距离，返回新位置。

        距离可为负值（后退），但偏移量不会越过区段起点。
        """
        ...

    @abstractmethod
    def to_absolute(self, pos: TrackPosition) -> float:
        """将 TrackPosition 转换为线路绝对里程 (m)。

        绝对里程从线路起点（Segment 1 offset=0）起算，
        用于跨区段距离计算和车钩力运算。
        """
        ...

    @abstractmethod
    def from_absolute(self, abs_pos: float) -> TrackPosition:
        """从线路绝对里程转换回 TrackPosition。

        用于微步积分后将绝对位置还原为区段坐标。
        """
        ...


# ── Mock 段定义 ──────────────────────────────────────────────

@dataclass
class MockSeg:
    """测试用区段定义。"""
    id: int
    length: float              # m
    limit: float               # m/s
    gradient: float            # ‰
    tunnel: bool
    curve_radius: Optional[float] = None  # m, None = 直线


# 测试用简化线路（约 3km，3 段，覆盖所有动力学场景）
MOCK_SEGMENTS = [
    MockSeg(id=1, length=1000.0, limit=22.22, gradient=0.0,   tunnel=False),                        # 80 km/h, 露天直线
    MockSeg(id=2, length=1000.0, limit=16.67, gradient=30.0,  tunnel=True,  curve_radius=400.0),     # 60 km/h, 隧道上坡+曲线 R=400m
    MockSeg(id=3, length=1000.0, limit=22.22, gradient=-15.0, tunnel=False),                        # 80 km/h, 下坡直线
]


class MockTrackQuery(ITrackQuery):
    """直线线路的 Mock 实现，用于开发期间独立测试。

    线路为直线，推进时 offset 线性累加，跨段自动切换 segment_id。
    """

    def __init__(self, segments=None):
        self.segments = segments or MOCK_SEGMENTS
        self._seg_by_id = {s.id: s for s in self.segments}

    def _get_seg(self, segment_id: int) -> MockSeg:
        return self._seg_by_id[segment_id]

    def get_speed_limit(self, pos: TrackPosition) -> float:
        return self._get_seg(pos.segment_id).limit

    def get_gradient(self, pos: TrackPosition) -> float:
        return self._get_seg(pos.segment_id).gradient

    def get_is_tunnel(self, pos: TrackPosition) -> bool:
        return self._get_seg(pos.segment_id).tunnel

    def get_curve_radius(self, pos: TrackPosition) -> Optional[float]:
        return self._get_seg(pos.segment_id).curve_radius

    def advance_position(self, pos: TrackPosition, distance: float) -> TrackPosition:
        """直线推进：offset 线性累加，跨段自动切换。

        支持正向推进（distance > 0）和反向推进（distance < 0）。
        超出线路终点时停在末端；退回起点之前时停在起点。
        """
        total_length = sum(s.length for s in self.segments)
        max_seg_id = max(s.id for s in self.segments)
        min_seg_id = min(s.id for s in self.segments)

        # 将当前位置转换为线路绝对里程
        abs_pos = self.to_absolute(pos)
        new_abs = abs_pos + distance

        # 边界裁剪
        if new_abs < 0.0:
            new_abs = 0.0
        if new_abs > total_length:
            new_abs = total_length

        return self.from_absolute(new_abs)

    def to_absolute(self, pos: TrackPosition) -> float:
        """将 TrackPosition 转换为线路绝对里程（从 Seg 1 起点起算）。"""
        offset = 0.0
        for seg in self.segments:
            if seg.id < pos.segment_id:
                offset += seg.length
            elif seg.id == pos.segment_id:
                offset += pos.offset
                break
        return offset

    def from_absolute(self, abs_pos: float) -> TrackPosition:
        """从线路绝对里程转换为 TrackPosition。"""
        remaining = abs_pos
        for seg in self.segments:
            if remaining <= seg.length:
                return TrackPosition(segment_id=seg.id, offset=remaining)
            remaining -= seg.length
        # 在终点：返回最后一段的终点
        last = self.segments[-1]
        return TrackPosition(segment_id=last.id, offset=last.length)
