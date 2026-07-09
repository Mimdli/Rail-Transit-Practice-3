"""TrackDataAdapter — 将 TrackData 适配为 ITrackQuery 接口

车辆仿真模块通过 ITrackQuery 接口访问线路数据。本适配器将旧版
TrackData（基于绝对浮点位置）包装为 ITrackQuery（基于 TrackPosition），
使新版 VehicleController 可以直接使用现有的 TrackData。

隧道和曲线半径数据：TrackData 当前未加载隧道/曲线数据。
可通过 tunnel_seg_ids / curve_seg_radius 参数补充，或使用默认值（无隧道、无曲线）。
"""

from typing import Optional, Dict, List, Set
from src.common.track_position import TrackPosition, ITrackQuery
from src.track.data import TrackData

# 共线岔点处区段端点里程允许的最大间隙 (m)
_OVERLAP_JUNCTION_TOL = 2.0


class TrackDataAdapter(ITrackQuery):
    """将 TrackData 适配为 ITrackQuery。

    通过 TrackData.segments 的拓扑邻居（正向/侧向）推进列车位置，
    支持主线与道岔分支独立走行；岔口走哪条路由 fork_routes 指定。

    Usage:
        track = TrackLoader().load_demo_data()
        adapter = TrackDataAdapter(track)
        adapter.set_fork_route(1, 6)  # Seg1 终点走侧线 Seg6
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
        # 岔口路由：parent_seg_id -> 离开该段时进入的 next_seg_id
        self._fork_routes: Dict[int, int] = {}

    # ── 岔口路由 ───────────────────────────────────────────────

    def set_fork_route(self, at_seg_id: int, next_seg_id: int) -> None:
        """指定列车离开 at_seg_id 终点时进入的相邻区段（正向或侧向）。"""
        self._fork_routes[at_seg_id] = next_seg_id

    def use_lateral_fork(self, at_seg_id: int) -> bool:
        """在 at_seg_id 终点优先走侧向分支。成功返回 True。"""
        seg = self._td._seg_map.get(at_seg_id)
        if seg is None or seg.end_lateral <= 0 or seg.end_lateral == 65535:
            return False
        self._fork_routes[at_seg_id] = seg.end_lateral
        return True

    def clear_fork_routes(self) -> None:
        self._fork_routes.clear()

    # ── 线路查询 ───────────────────────────────────────────────

    def get_speed_limit(self, pos: TrackPosition) -> float:
        abs_pos = self.to_absolute(pos)
        return self._td.get_speed_limit_at(abs_pos)

    def get_gradient(self, pos: TrackPosition) -> float:
        abs_pos = self.to_absolute(pos)
        return self._td.get_gradient_at(abs_pos, seg_id=pos.segment_id)

    def get_is_tunnel(self, pos: TrackPosition) -> bool:
        return pos.segment_id in self._tunnel_seg_ids

    def get_curve_radius(self, pos: TrackPosition) -> Optional[float]:
        return self._curve_seg_radius.get(pos.segment_id)

    # ── 位置坐标转换 ───────────────────────────────────────────

    def advance_position(self, pos: TrackPosition, distance: float) -> TrackPosition:
        """沿当前区段拓扑推进，支持跨段与道岔分支。"""
        if distance == 0:
            return pos

        seg_map = self._td._seg_map
        seg_id = pos.segment_id
        offset = pos.offset
        remaining = distance

        while abs(remaining) > 1e-9:
            seg = seg_map.get(seg_id)
            if seg is None:
                return pos

            if remaining > 0:
                room = seg.length - offset
                if remaining <= room + 1e-9:
                    return TrackPosition(seg_id, offset + remaining)
                end_abs = seg.abs_start + seg.length
                remaining -= room
                next_id = self._pick_forward_exit(seg)
                if next_id is None or next_id not in seg_map:
                    return TrackPosition(seg_id, seg.length)
                next_seg = seg_map[next_id]
                seg_id = next_id
                offset = max(0.0, min(end_abs - next_seg.abs_start, next_seg.length))
            else:
                if -remaining <= offset + 1e-9:
                    return TrackPosition(seg_id, offset + remaining)
                remaining += offset
                prev_id = self._next_backward(seg)
                if prev_id is None or prev_id not in seg_map:
                    return TrackPosition(seg_id, 0.0)
                prev_seg = seg_map[prev_id]
                seg_id = prev_id
                offset = prev_seg.length

        return TrackPosition(seg_id, offset)

    def _forward_exit_candidates(self, seg) -> List[int]:
        if seg.seg_id in self._fork_routes:
            chosen = self._fork_routes[seg.seg_id]
            if chosen > 0 and chosen != 65535:
                return [chosen]
        cands = []
        for nid in (seg.end_neighbor, seg.end_lateral):
            if nid > 0 and nid != 65535:
                cands.append(nid)
        return cands

    def _pick_forward_exit(self, seg) -> Optional[int]:
        """离开区段时选择前向出口，优先能抵达更远终点的路径。"""
        cands = self._forward_exit_candidates(seg)
        if cands:
            if len(cands) == 1:
                return cands[0]
            memo: Dict[int, float] = {}
            return max(cands, key=lambda nid: self._max_reachable_end_abs(nid, memo))
        return self._overlap_forward_at(seg.abs_start + seg.length, exclude=seg.seg_id)

    def _overlap_forward_at(self, abs_pos: float, exclude: int = 0) -> Optional[int]:
        """在共线岔点处，按绝对里程匹配可接续的下一段。"""
        cands = []
        for s in self._td.segments:
            if s.seg_id == exclude:
                continue
            if (s.abs_start - _OVERLAP_JUNCTION_TOL
                    <= abs_pos
                    <= s.abs_start + s.length + _OVERLAP_JUNCTION_TOL):
                cands.append(s.seg_id)
        if not cands:
            return None
        memo: Dict[int, float] = {}
        return max(cands, key=lambda nid: self._max_reachable_end_abs(nid, memo))

    def _max_reachable_end_abs(self, seg_id: int, memo: Dict[int, float],
                              visiting: Optional[Set[int]] = None) -> float:
        if seg_id in memo:
            return memo[seg_id]
        seg = self._td._seg_map.get(seg_id)
        if seg is None:
            return 0.0
        if visiting is None:
            visiting = set()
        if seg_id in visiting:
            return seg.abs_start + seg.length
        visiting.add(seg_id)

        best = seg.abs_start + seg.length
        for nid in self._forward_exit_candidates(seg):
            best = max(best, self._max_reachable_end_abs(nid, memo, visiting))
        overlap = self._overlap_forward_at(best, exclude=seg_id)
        if overlap is not None:
            best = max(best, self._max_reachable_end_abs(overlap, memo, visiting))

        visiting.remove(seg_id)
        memo[seg_id] = best
        return best

    def _next_backward(self, seg) -> Optional[int]:
        for nid in (seg.start_neighbor, seg.start_lateral):
            if nid > 0 and nid != 65535:
                return nid
        return None

    def to_absolute(self, pos: TrackPosition) -> float:
        """TrackPosition → 线路绝对里程 (m)。"""
        seg = self._td._seg_map.get(pos.segment_id)
        if seg is None:
            return pos.offset
        return seg.abs_start + pos.offset

    def from_absolute(self, abs_pos: float, hint_seg_id: int = 0) -> TrackPosition:
        """线路绝对里程 → TrackPosition。

        多段共线（主线/侧线重叠里程）时优先 hint_seg_id，否则取 seg_id 最小者。
        """
        if hint_seg_id:
            seg = self._td._seg_map.get(hint_seg_id)
            if seg is not None and seg.abs_start <= abs_pos < seg.abs_start + seg.length:
                return TrackPosition(hint_seg_id, abs_pos - seg.abs_start)

        total = self._td.total_length()
        if abs_pos >= total:
            last = max(self._td.segments, key=lambda s: s.abs_start + s.length)
            return TrackPosition(segment_id=last.seg_id, offset=last.length)

        candidates = [
            s for s in self._td.segments
            if s.abs_start <= abs_pos < s.abs_start + s.length
        ]
        if candidates:
            seg = min(candidates, key=lambda s: s.seg_id)
            return TrackPosition(segment_id=seg.seg_id, offset=abs_pos - seg.abs_start)

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
