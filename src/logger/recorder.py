"""运行事件记录器。"""

import csv
import os
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class LogEvent:
    """单条结构化运行事件。"""
    timestamp: float          # 相对时间 (s)
    event_type: str           # 事件类型
    description: str          # 事件描述
    position: float = 0.0     # 事件发生位置
    speed: float = 0.0        # 事件发生时速度
    train_id: str = ""        # 关联列车
    source: str = "system"   # 事件来源模块
    severity: str = "INFO"   # INFO / WARNING / CRITICAL
    entity_id: str = ""       # 信号机、区段或车站等对象 ID


class Recorder:
    """事件记录器"""

    def __init__(self, log_dir: str = "logs"):
        self.events: List[LogEvent] = []
        self._start_time: float = 0.0
        self.log_dir = log_dir
        self.log_path: Optional[str] = None
        self._log_file = None
        self._writer = None
        self.is_active: bool = False

    def start(self):
        """开始记录"""
        if self.is_active:
            return
        self.events.clear()
        self._start_time = 0.0
        self.is_active = True
        self._open_log_file()

    def record(self, event_type: str, description: str,
               position: float = 0.0, speed: float = 0.0, *,
               train_id: str = "", source: str = "system",
               severity: Optional[str] = None, entity_id: str = ""):
        """记录一条事件，旧的四个位置参数保持兼容。"""
        if not self.is_active:
            return
        event = LogEvent(
            timestamp=self._start_time,
            event_type=event_type,
            description=description,
            position=position,
            speed=speed,
            train_id=train_id,
            source=source or "system",
            severity=(severity or self._infer_severity(event_type)).upper(),
            entity_id=entity_id,
        )
        self.events.append(event)
        self._write_event(event)

    def step(self, dt: float):
        """更新时间"""
        if not self.is_active:
            return
        self._start_time += dt

    def get_events_by_type(self, event_type: str) -> List[LogEvent]:
        """按类型筛选事件"""
        return [e for e in self.events if e.event_type == event_type]

    def query(self, *, event_type: str = "", train_id: str = "",
              source: str = "", severity: str = "") -> List[LogEvent]:
        """按结构化字段组合筛选，空字段表示不限制。"""
        severity = severity.upper()
        return [
            event for event in self.events
            if (not event_type or event.event_type == event_type)
            and (not train_id or event.train_id == train_id)
            and (not source or event.source == source)
            and (not severity or event.severity == severity)
        ]

    def get_summary(self) -> dict:
        """获取运行摘要"""
        overspeed = self.get_events_by_type("超速")
        red_light = self.get_events_by_type("红灯违规")
        emergency_brake = self.get_events_by_type("紧急制动")
        collisions = self.get_events_by_type("列车碰撞")
        departures = self.get_events_by_type("发车")
        arrivals = self.get_events_by_type("到站")

        return {
            "总事件数": len(self.events),
            "警告事件数": len(self.query(severity="WARNING")),
            "严重事件数": len(self.query(severity="CRITICAL")),
            "超速次数": len(overspeed),
            "红灯违规次数": len(red_light),
            "紧急制动次数": len(emergency_brake),
            "碰撞次数": len(collisions),
            "发车次数": len(departures),
            "到站次数": len(arrivals),
        }

    def clear(self):
        """清除所有记录"""
        self.events.clear()
        self._start_time = 0.0

    def close(self):
        """关闭日志文件"""
        if self._log_file:
            self._log_file.close()
            self._log_file = None
            self._writer = None
        self.is_active = False

    def _build_log_path(self, log_dir: str) -> str:
        """生成本次运行的日志文件路径"""
        # Windows 时钟精度可能返回相同微秒，文件存在时追加序号。
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(log_dir, f"run_{timestamp}.csv")
        sequence = 1
        while os.path.exists(path):
            path = os.path.join(log_dir, f"run_{timestamp}_{sequence}.csv")
            sequence += 1
        return path

    def _open_log_file(self):
        """打开 CSV 日志文件并写入表头"""
        os.makedirs(self.log_dir, exist_ok=True)
        self.log_path = self._build_log_path(self.log_dir)
        self._log_file = open(self.log_path, "w", newline="", encoding="utf-8-sig")
        self._writer = csv.writer(self._log_file)
        self._writer.writerow([
            "timestamp", "event_type", "severity", "source", "train_id",
            "entity_id", "description", "position_m", "speed_m_s",
        ])
        self._log_file.flush()

    def _write_event(self, event: LogEvent):
        """把事件同步写入 CSV，避免异常退出时丢失日志"""
        if not self._writer or not self._log_file:
            return
        self._writer.writerow([
            f"{event.timestamp:.1f}",
            event.event_type,
            event.severity,
            event.source,
            event.train_id,
            event.entity_id,
            event.description,
            f"{event.position:.3f}",
            f"{event.speed:.3f}",
        ])
        self._log_file.flush()

    @staticmethod
    def _infer_severity(event_type: str) -> str:
        """为旧调用自动补齐严重等级。"""
        if event_type in ("紧急制动", "红灯违规", "列车碰撞"):
            return "CRITICAL"
        if event_type in ("超速", "安全", "告警", "安全防护", "列车接近"):
            return "WARNING"
        return "INFO"
