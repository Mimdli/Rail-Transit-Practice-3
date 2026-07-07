"""轨道线路数据结构 — TODO: 定义线路数据类"""

from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Station:
    """车站"""
    name: str
    position: float


@dataclass
class Platform:
    """站台"""
    station_name: str
    position: float
    side: str
    length: float = 120.0


@dataclass
class Segment:
    """线路区段"""
    seg_id: str
    start_pos: float
    end_pos: float


@dataclass
class SpeedLimit:
    """限速区段"""
    start_pos: float
    end_pos: float
    speed_limit: float


@dataclass
class Gradient:
    """坡度区段"""
    start_pos: float
    end_pos: float
    gradient: float


@dataclass
class Signal:
    """信号机"""
    signal_id: str
    position: float
    direction: str


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
        raise NotImplementedError

    def get_gradient_at(self, position: float) -> float:
        """获取指定位置的坡度"""
        raise NotImplementedError

    def get_station_at(self, position: float) -> Optional[Station]:
        """获取指定位置的车站"""
        raise NotImplementedError

    def get_platform_side_at(self, position: float) -> str:
        """获取指定位置的站台侧"""
        raise NotImplementedError

    def get_nearest_station_ahead(self, position: float) -> Optional[Station]:
        """获取前方最近的车站"""
        raise NotImplementedError

    def total_length(self) -> float:
        """获取线路总长"""
        raise NotImplementedError
