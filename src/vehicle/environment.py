"""环境系统 — 天气类型、粘着系数与隧道/露天感知

包含:
    WeatherType — 天气类型枚举（干燥/雨/雪）
    IEnvironmentQuery — 环境数据查询接口（车辆模块对环境数据的唯一依赖）
    MockEnvironment — 开发期间使用的 Mock 实现（按天气+隧道位置返回粘着系数）
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Optional
from src.common.track_position import TrackPosition, ITrackQuery


# ═══════════════════════════════════════════════════════════════
# 天气类型
# ═══════════════════════════════════════════════════════════════

class WeatherType(Enum):
    """天气类型。"""
    DRY = "dry"    # 干燥
    RAIN = "rain"  # 雨
    SNOW = "snow"  # 雪


# ═══════════════════════════════════════════════════════════════
# 环境查询接口
# ═══════════════════════════════════════════════════════════════

class IEnvironmentQuery(ABC):
    """车辆模块对环境数据的唯一依赖。

    所有外部环境依赖通过此接口访问，开发期间使用 Mock 实现。

    接口设计原则:
        - pos 参数为可选：传入 None 时返回全局默认值（兼容简单调用）；
          传入 TrackPosition 时可按位置（隧道/露天、区段等）返回差异化数值。
        - 未来可扩展：温度、风速、能见度等。
    """

    @abstractmethod
    def get_adhesion_coefficient(self, pos: Optional[TrackPosition] = None) -> float:
        """返回当前轮轨粘着系数。

        典型值: 干燥=0.18, 雨=0.10, 雪=0.06。
        隧道内因轨面潮湿，粘着系数通常低于露天段。

        Args:
            pos: 查询位置。None 时返回全局默认值；
                 传入 TrackPosition 时可按位置返回差异化数值。

        Returns:
            粘着系数 (0.0 ~ 1.0)。
        """
        ...


# ═══════════════════════════════════════════════════════════════
# Mock 实现（开发期间使用）
# ═══════════════════════════════════════════════════════════════

# 隧道内粘着系数衰减因子（模拟隧道内轨面潮湿导致的粘着降低）
_TUNNEL_ADHESION_FACTOR = 0.8


class MockEnvironment(IEnvironmentQuery):
    """Mock 环境实现，用于开发期间独立测试。

    基础粘着系数由天气决定，传入 TrackPosition 时自动感知隧道/露天：
        - 露天：返回天气基础粘着系数
        - 隧道内：基础粘着系数 × 0.8（模拟较高的湿度/轨面潮湿）

    Usage:
        # 简单用法（全局值，不区分隧道）
        env = MockEnvironment(WeatherType.DRY)
        mu = env.get_adhesion_coefficient()  # 0.18

        # 按位置查询（感知隧道）
        env = MockEnvironment(WeatherType.RAIN, track)
        mu = env.get_adhesion_coefficient(head_position)  # 隧道内 0.10*0.8=0.08
    """

    # 天气 → 基础粘着系数映射
    ADHESION_MAP = {
        WeatherType.DRY: 0.18,
        WeatherType.RAIN: 0.10,
        WeatherType.SNOW: 0.06,
    }

    def __init__(self, weather: WeatherType = WeatherType.DRY,
                 track: Optional[ITrackQuery] = None):
        """
        Args:
            weather: 天气类型（决定基础粘着系数）。
            track: 线路查询接口（用于判断隧道/露天）。None 时不感知隧道。
        """
        self.weather = weather
        self.track = track

    def get_adhesion_coefficient(self, pos: Optional[TrackPosition] = None) -> float:
        """返回粘着系数。

        当 pos 和 track 均不为 None 时，隧道内粘着系数衰减 20%。
        """
        base = self.ADHESION_MAP[self.weather]

        # 隧道感知：检测位置是否在隧道内
        if pos is not None and self.track is not None:
            if self.track.get_is_tunnel(pos):
                return base * _TUNNEL_ADHESION_FACTOR

        return base
