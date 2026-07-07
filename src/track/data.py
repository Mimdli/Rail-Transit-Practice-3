"""轨道线路数据结构"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Station:
    """车站"""
    name: str
    position: float              # 车站位置 (m)
    platform_count: int = 1


@dataclass
class Platform:
    """站台"""
    station_name: str
    position: float              # 站台位置 (m)
    side: str                    # 站台侧: "left" / "right"
    length: float = 120.0        # 站台长度 (m)


@dataclass
class Segment:
    """线路区段"""
    seg_id: str
    start_pos: float             # 起点位置 (m)
    end_pos: float               # 终点位置 (m)


@dataclass
class SpeedLimit:
    """限速区段"""
    start_pos: float
    end_pos: float
    speed_limit: float           # 限速值 (m/s)


@dataclass
class Gradient:
    """坡度区段"""
    start_pos: float
    end_pos: float
    gradient: float              # 坡度值 (‰)


@dataclass
class Signal:
    """信号机"""
    signal_id: str
    position: float              # 位置 (m)
    direction: str               # 方向: "up" / "down"


class TrackData:
    """线路数据集合"""

    def __init__(self):
        self.stations: List[Station] = []
        self.platforms: List[Platform] = []
        self.segments: List[Segment] = []
        self.speed_limits: List[SpeedLimit] = []
        self.gradients: List[Gradient] = []
        self.signals: List[Signal] = []

    def get_speed_limit_at(self, position: float) -> float:
        """获取指定位置的限速"""
        for sl in self.speed_limits:
            if sl.start_pos <= position <= sl.end_pos:
                return sl.speed_limit
        return 22.0  # 默认限速

    def get_gradient_at(self, position: float) -> float:
        """获取指定位置的坡度"""
        for g in self.gradients:
            if g.start_pos <= position <= g.end_pos:
                return g.gradient
        return 0.0

    def get_station_at(self, position: float) -> Optional[Station]:
        """获取指定位置的车站"""
        for station in self.stations:
            if abs(station.position - position) < 50:
                return station
        return None

    def get_platform_side_at(self, position: float) -> str:
        """获取指定位置的站台侧"""
        for p in self.platforms:
            if abs(p.position - position) < p.length / 2:
                return p.side
        return ""

    def get_nearest_station_ahead(self, position: float) -> Optional[Station]:
        """获取前方最近的车站"""
        ahead = [s for s in self.stations if s.position > position]
        if not ahead:
            return None
        return min(ahead, key=lambda s: s.position - position)

    def total_length(self) -> float:
        """获取线路总长"""
        if not self.segments:
            return 0.0
        return max(seg.end_pos for seg in self.segments)
