"""TrackDataAdapter — 将 TrackData 适配为 ITrackQuery 接口

车辆仿真模块通过 ITrackQuery 接口访问线路数据。本适配器将旧版
TrackData（基于绝对浮点位置）包装为 ITrackQuery（基于 TrackPosition），
使新版 VehicleController 可以直接使用现有的 TrackData。

隧道和曲线半径数据：TrackData 当前未加载隧道/曲线数据。
可通过 tunnel_seg_ids / curve_seg_radius 参数补充，或使用默认值（无隧道、无曲线）。
"""

from typing import Optional, Dict
from src.common.track_position import TrackPosition, ITrackQuery
from src.track.data import TrackData


class TrackDataAdapter(ITrackQuery):
    """将 TrackData 适配为 ITrackQuery。

    通过 TrackData.segments 的 abs_start + length 信息，
    将 TrackPosition (segment_id, offset) 与绝对里程互相转换。

    Usage:
        track = TrackLoader().load_demo_data()
        adapter = TrackDataAdapter(track)
        controller = VehicleController(consist, adapter, env)
    """

    def __init__(self, track_data: TrackData,
                 tunnel_seg_ids: Optional[set] = None,
                 curve_seg_radius: Optional[Dict[int, float]] = None):
        """
        Args:
            track_data: 已构建坐标的 TrackData 实例。
            tunnel_seg_ids: 隧道区段 ID 集合。None 表示无隧道。
            curve_seg_radius: {seg_id: radius_m} 曲线半径映射。None 表示无曲线。
        """
        self._td = track_data
        self._tunnel_seg_ids = tunnel_seg_ids or set()
        self._curve_seg_radius = curve_seg_radius or {}

    # ── 线路查询 ───────────────────────────────────────────────

    def get_speed_limit(self, pos: TrackPosition) -> float:
        abs_pos = self.to_absolute(pos)
        return self._td.get_speed_limit_at(abs_pos)

    def get_gradient(self, pos: TrackPosition) -> float:
        abs_pos = self.to_absolute(pos)
        return self._td.get_gradient_at(abs_pos)

    def get_is_tunnel(self, pos: TrackPosition) -> bool:
        return pos.segment_id in self._tunnel_seg_ids

    def get_curve_radius(self, pos: TrackPosition) -> Optional[float]:
        return self._curve_seg_radius.get(pos.segment_id)

    # ── 位置坐标转换 ───────────────────────────────────────────

    def advance_position(self, pos: TrackPosition, distance: float) -> TrackPosition:
        """沿线路推进指定距离。

        通过绝对坐标转换实现，支持跨区段推进。
        超出线路终点时钳位到终点；允许起点之前的负位置（列车尾部）。
        """
        total = self._td.total_length()
        abs_pos = self.to_absolute(pos)
        new_abs = abs_pos + distance
        if new_abs > total:
            new_abs = total
        return self.from_absolute(new_abs)

    def to_absolute(self, pos: TrackPosition) -> float:
        """TrackPosition → 线路绝对里程 (m)。"""
        seg = self._td._seg_map.get(pos.segment_id)
        if seg is None:
            # 尝试回退：如果 segment_id 不存在，返回 offset 作为绝对位置
            return pos.offset
        return seg.abs_start + pos.offset

    def from_absolute(self, abs_pos: float) -> TrackPosition:
        """线路绝对里程 → TrackPosition。

        允许第一段出现负 offset（列车尾部可能在线路起点之前）。
        """
        total = self._td.total_length()
        if abs_pos >= total:
            last = max(self._td.segments, key=lambda s: s.abs_start + s.length)
            return TrackPosition(segment_id=last.seg_id, offset=last.length)

        for seg in self._td.segments:
            seg_end = seg.abs_start + seg.length
            if seg.abs_start <= abs_pos < seg_end:
                return TrackPosition(segment_id=seg.seg_id,
                                     offset=abs_pos - seg.abs_start)

        # abs_pos < 0 或落在段间隙中 → 归入根区段（abs_start == 0），允许负 offset
        if self._td.segments:
            root_seg = self._td.segments[0]
            for seg in self._td.segments:
                if abs(seg.abs_start) < 0.01:
                    root_seg = seg
                    break
            return TrackPosition(segment_id=root_seg.seg_id, offset=abs_pos)
        return TrackPosition(segment_id=1, offset=abs_pos)

    # ── 便捷属性 ────────────────────────────────────────────────

    @property
    def track_data(self) -> TrackData:
        """获取底层 TrackData，供 UI 层访问车站/信号等原始数据。"""
        return self._td

    def total_length(self) -> float:
        return self._td.total_length()
