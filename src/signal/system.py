"""信号系统 — TODO: 实现信号状态与安全约束"""

from enum import Enum


class SignalAspect(Enum):
    """信号显示状态"""
    GREEN = "绿灯"
    YELLOW = "黄灯"
    RED = "红灯"


class SignalSystem:
    """信号系统"""

    def __init__(self):
        self.yellow_speed_limit: float = 10.0

    def get_aspect_at(self, position: float, signals: list):
        """获取指定位置的信号状态"""
        raise NotImplementedError

    def check_red_signal_ahead(self, position: float, signals: list, look_ahead: float = 200.0) -> bool:
        """检查前方是否有红灯"""
        raise NotImplementedError

    def get_effective_speed_limit(self, position: float, track_speed_limit: float, signals: list) -> float:
        """获取有效限速（综合考虑线路限速和信号限速）"""
        raise NotImplementedError
