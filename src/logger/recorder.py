"""运行事件记录器"""

from dataclasses import dataclass, field
from typing import List
from datetime import datetime


@dataclass
class LogEvent:
    """单条运行事件"""
    timestamp: float          # 相对时间 (s)
    event_type: str           # 事件类型
    description: str          # 事件描述
    position: float = 0.0     # 事件发生位置
    speed: float = 0.0        # 事件发生时速度


class Recorder:
    """事件记录器"""

    def __init__(self):
        self.events: List[LogEvent] = []
        self._start_time: float = 0.0

    def start(self):
        """开始记录"""
        self.events.clear()
        self._start_time = 0.0

    def record(self, event_type: str, description: str, position: float = 0.0, speed: float = 0.0):
        """记录一条事件"""
        event = LogEvent(
            timestamp=self._start_time,
            event_type=event_type,
            description=description,
            position=position,
            speed=speed,
        )
        self.events.append(event)

    def step(self, dt: float):
        """更新时间"""
        self._start_time += dt

    def get_events_by_type(self, event_type: str) -> List[LogEvent]:
        """按类型筛选事件"""
        return [e for e in self.events if e.event_type == event_type]

    def get_summary(self) -> dict:
        """获取运行摘要"""
        overspeed = self.get_events_by_type("超速")
        red_light = self.get_events_by_type("红灯违规")
        emergency_brake = self.get_events_by_type("紧急制动")
        departures = self.get_events_by_type("发车")
        arrivals = self.get_events_by_type("到站")

        return {
            "总事件数": len(self.events),
            "超速次数": len(overspeed),
            "红灯违规次数": len(red_light),
            "紧急制动次数": len(emergency_brake),
            "发车次数": len(departures),
            "到站次数": len(arrivals),
        }

    def clear(self):
        """清除所有记录"""
        self.events.clear()
        self._start_time = 0.0
