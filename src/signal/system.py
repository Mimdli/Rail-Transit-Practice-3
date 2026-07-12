"""信号系统 — 基本信号状态与安全约束"""

from enum import Enum
from typing import Optional


class SignalAspect(Enum):
    """信号显示状态"""
    GREEN = "绿灯"       # 允许正常运行
    YELLOW = "黄灯"      # 允许运行，需降低速度
    RED = "红灯"         # 禁止越过


PROTOCOL_ASPECT_MAP = {
    0x01: SignalAspect.RED,     # 红灯
    0x02: SignalAspect.YELLOW,  # 黄灯
    0x03: SignalAspect.YELLOW,  # 红黄灯，按限制运行处理
    0x04: SignalAspect.GREEN,   # 绿灯
    0x05: SignalAspect.RED,     # 黄灭，按故障防护处理
    0x06: SignalAspect.RED,     # 红灭，按故障防护处理
    0x07: SignalAspect.RED,     # 绿灭，按故障防护处理
    0x08: SignalAspect.YELLOW,  # 白灯，暂按特殊限制允许处理
    0x09: SignalAspect.RED,     # 红断，按故障防护处理
    0x0A: SignalAspect.RED,     # 蓝灯，按禁止通行处理
    0x10: SignalAspect.RED,     # 绿断，按故障防护处理
    0x20: SignalAspect.RED,     # 黄断，按故障防护处理
    0x30: SignalAspect.RED,     # 白断，按故障防护处理
}


def aspect_from_protocol(value: int) -> SignalAspect:
    """将协议灯色码归约为仿真内部红黄绿三态"""
    return PROTOCOL_ASPECT_MAP.get(value, SignalAspect.RED)


class SignalSystem:
    """信号系统"""

    def __init__(self):
        self.yellow_speed_limit: float = 10.0   # 黄灯限速 (m/s)
        self.signal_range: float = 50.0         # 信号机当前位置判定范围 (m)
        self.approach_range: float = 200.0      # 前方信号防护范围 (m)
        self._aspects: dict[str, SignalAspect] = {}

    def set_signal_aspect(self, signal_id: str, aspect: SignalAspect):
        """设置指定信号机的显示状态"""
        self._aspects[signal_id] = aspect

    def set_signal_aspect_from_protocol(self, signal_id: str, value: int):
        """按协议灯色码设置信号机显示状态"""
        self.set_signal_aspect(signal_id, aspect_from_protocol(value))

    def set_signal_aspects(self, aspects: dict[str, SignalAspect]):
        """批量设置信号机显示状态"""
        self._aspects.update(aspects)

    def clear_signal_aspects(self):
        """清空已缓存的信号显示状态"""
        self._aspects.clear()

    def update_aspects_by_occupancy(
        self, signals: list, train_positions: list[float], direction: str = "up"
    ):
        """根据前车占用的闭塞分区自动生成信号显示"""
        ordered_signals = self._sort_signals(signals, direction)
        occupied_blocks = self._get_occupied_block_indexes(ordered_signals, train_positions, direction)

        for index, sig in enumerate(ordered_signals):
            # 当前信号机防护的下一个闭塞分区被占用时显示红灯。
            if index in occupied_blocks:
                aspect = SignalAspect.RED
            elif index + 1 in occupied_blocks:
                aspect = SignalAspect.YELLOW
            else:
                aspect = SignalAspect.GREEN
            self.set_signal_aspect(sig.signal_id, aspect)

    def update_from_dispatch(self, signals: list, track,
                             occupancy: dict[int, frozenset[str]],
                             locks: dict[int, str]):
        """根据区段占用和调度进路锁闭生成联锁信号显示。

        信号仅对占用亮红灯，锁闭由联锁系统保证互斥，
        不因段未被锁而亮红灯，避免滚动进路死锁。
        """
        if not signals:
            return
        for signal in signals:
            protected = self._protected_segment_id(signal, track)
            if protected in occupancy:
                aspect = SignalAspect.RED
            else:
                aspect = SignalAspect.GREEN
            self.set_signal_aspect(signal.signal_id, aspect)

        # 同方向红灯的前一架信号显示黄灯预告。
        for direction in ("down", "up"):
            ordered = [signal for signal in signals
                       if self._normalize_direction(signal.direction) == direction]
            ordered.sort(key=lambda signal: signal.position,
                         reverse=direction == "up")
            for current, ahead in zip(ordered, ordered[1:]):
                if (self.get_signal_aspect(current) != SignalAspect.RED
                        and self.get_signal_aspect(ahead) == SignalAspect.RED):
                    self.set_signal_aspect(current.signal_id, SignalAspect.YELLOW)

    def _protected_segment_id(self, signal, track) -> int:
        segment = track._seg_map.get(signal.seg_id)
        if segment is None:
            return signal.seg_id
        direction = self._normalize_direction(signal.direction)
        neighbor = (segment.end_neighbor if direction == "down"
                    else segment.start_neighbor)
        if neighbor and neighbor != 65535 and neighbor in track._seg_map:
            return neighbor
        return signal.seg_id

    @staticmethod
    def _normalize_direction(direction: str) -> str:
        value = str(direction or "").lower()
        return "down" if value in ("down", "下行", "1") else "up"

    def get_nearest_signal_for_direction(
        self, position: float, direction: int, signals: list,
        look_ahead: Optional[float] = None,
        allowed_segment_ids: Optional[set[int]] = None,
    ):
        """获取物理运行方向前方最近的同向信号机。"""
        max_distance = self.approach_range if look_ahead is None else look_ahead
        expected = "down" if direction >= 0 else "up"
        candidates = []
        for signal in signals:
            if (allowed_segment_ids is not None
                    and signal.seg_id not in allowed_segment_ids):
                continue
            if self._normalize_direction(signal.direction) != expected:
                continue
            distance = direction * (signal.position - position)
            if 0 < distance <= max_distance:
                candidates.append((distance, signal))
        return min(candidates, key=lambda item: item[0])[1] if candidates else None

    def get_effective_speed_limit_for_direction(
        self, position: float, direction: int, track_speed_limit: float,
        signals: list,
    ) -> float:
        signal = self.get_nearest_signal_for_direction(
            position, direction, signals)
        if signal and self.get_signal_aspect(signal) == SignalAspect.YELLOW:
            return min(track_speed_limit, self.yellow_speed_limit)
        return track_speed_limit

    def _sort_signals(self, signals: list, direction: str) -> list:
        """按运行方向排列信号机"""
        reverse = direction == "down"
        return sorted(signals, key=lambda sig: sig.position, reverse=reverse)

    def _get_occupied_block_indexes(
        self, ordered_signals: list, train_positions: list[float], direction: str
    ) -> set[int]:
        """计算被前车占用的闭塞分区序号"""
        occupied_blocks = set()
        for train_pos in train_positions:
            block_index = self._find_block_index(ordered_signals, train_pos, direction)
            if block_index is not None:
                occupied_blocks.add(block_index)
        return occupied_blocks

    def _find_block_index(self, ordered_signals: list, position: float, direction: str) -> Optional[int]:
        """查找位置所在的闭塞分区"""
        if not ordered_signals:
            return None

        for index, sig in enumerate(ordered_signals):
            next_sig = ordered_signals[index + 1] if index + 1 < len(ordered_signals) else None
            if self._is_position_in_block(position, sig, next_sig, direction):
                return index
        return None

    def _is_position_in_block(self, position: float, start_signal, next_signal, direction: str) -> bool:
        """判断位置是否落在某个信号机防护的闭塞分区内"""
        if direction == "down":
            end_pos = next_signal.position if next_signal else float("-inf")
            return end_pos < position <= start_signal.position

        end_pos = next_signal.position if next_signal else float("inf")
        return start_signal.position <= position < end_pos

    def get_signal_aspect(self, signal) -> SignalAspect:
        """获取信号机显示状态，未配置时按红灯防护处理"""
        return self._aspects.get(signal.signal_id, SignalAspect.RED)

    def get_nearest_signal_ahead(
        self, position: float, signals: list, look_ahead: Optional[float] = None
    ):
        """获取前方防护范围内最近的信号机"""
        max_distance = self.approach_range if look_ahead is None else look_ahead
        candidates = [
            sig for sig in signals
            if 0 < sig.position - position <= max_distance
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda sig: sig.position - position)

    def get_aspect_at(self, position: float, signals: list) -> SignalAspect:
        """获取指定位置附近的信号显示状态"""
        for sig in signals:
            if abs(sig.position - position) <= self.signal_range:
                return self.get_signal_aspect(sig)
        return SignalAspect.GREEN

    def check_red_signal_ahead(self, position: float, signals: list, look_ahead: float = 200.0) -> bool:
        """检查前方是否有红灯"""
        return self.check_signal_ahead(position, signals, SignalAspect.RED, look_ahead)

    def check_signal_ahead(
        self, position: float, signals: list, aspect: SignalAspect, look_ahead: float = 200.0
    ) -> bool:
        """检查前方防护范围内是否存在指定显示状态的信号机"""
        for sig in signals:
            if 0 < sig.position - position <= look_ahead:
                if self.get_signal_aspect(sig) == aspect:
                    return True
        return False

    def get_effective_speed_limit(self, position: float, track_speed_limit: float, signals: list) -> float:
        """获取有效限速（综合考虑线路限速和信号限速）"""
        nearest_signal = self.get_nearest_signal_ahead(position, signals)
        aspect = self.get_signal_aspect(nearest_signal) if nearest_signal else self.get_aspect_at(position, signals)
        if aspect == SignalAspect.YELLOW:
            return min(track_speed_limit, self.yellow_speed_limit)
        return track_speed_limit
