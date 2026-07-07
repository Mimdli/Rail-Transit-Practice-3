"""运行事件记录器 — TODO: 实现运行事件记录"""

from dataclasses import dataclass
from typing import List


@dataclass
class LogEvent:
    """单条运行事件"""
    timestamp: float
    event_type: str
    description: str
    position: float = 0.0
    speed: float = 0.0


class Recorder:
    """事件记录器"""

    def __init__(self):
        self.events: List[LogEvent] = []

    def start(self):
        """开始记录"""
        raise NotImplementedError

    def record(self, event_type: str, description: str, position: float = 0.0, speed: float = 0.0):
        """记录一条事件"""
        raise NotImplementedError

    def step(self, dt: float):
        """更新时间"""
        raise NotImplementedError

    def get_events_by_type(self, event_type: str) -> List[LogEvent]:
        """按类型筛选事件"""
        raise NotImplementedError

    def get_summary(self) -> dict:
        """获取运行摘要"""
        raise NotImplementedError

    def clear(self):
        """清除所有记录"""
        raise NotImplementedError
