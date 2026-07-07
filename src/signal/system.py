"""信号系统 — 基本信号状态与安全约束"""

from enum import Enum


class SignalAspect(Enum):
    """信号显示状态"""
    GREEN = "绿灯"       # 允许正常运行
    YELLOW = "黄灯"      # 允许运行，需降低速度
    RED = "红灯"         # 禁止越过


class SignalSystem:
    """信号系统"""

    def __init__(self):
        self.yellow_speed_limit: float = 10.0   # 黄灯限速 (m/s)

    def get_aspect_at(self, position: float, signals: list) -> SignalAspect:
        """获取指定位置的信号状态"""
        for sig in signals:
            if abs(sig.position - position) < 50:
                return SignalAspect.RED
        return SignalAspect.GREEN

    def check_red_signal_ahead(self, position: float, signals: list, look_ahead: float = 200.0) -> bool:
        """检查前方是否有红灯"""
        for sig in signals:
            if sig.position > position and sig.position - position < look_ahead:
                return True
        return False

    def get_effective_speed_limit(self, position: float, track_speed_limit: float, signals: list) -> float:
        """获取有效限速（综合考虑线路限速和信号限速）"""
        aspect = self.get_aspect_at(position, signals)
        if aspect == SignalAspect.YELLOW:
            return min(track_speed_limit, self.yellow_speed_limit)
        return track_speed_limit
