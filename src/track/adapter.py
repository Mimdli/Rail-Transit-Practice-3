"""TrackDataAdapter — 将 TrackData 适配为 ITrackQuery 接口

车辆仿真模块通过 ITrackQuery 接口访问线路数据。本适配器将旧版
TrackData（基于绝对浮点位置）包装为 ITrackQuery（基于 TrackPosition），
使新版 VehicleController 可以直接使用现有的 TrackData。

道岔消歧（v2）：from_absolute() 在道岔重叠区域使用 3 层策略选择 segment：
  1. 活动进路（Route）匹配
  2. 主线启发式（_main_line_seg_ids，仅 forward neighbor 链）
  3. 第一个候选（兜底）

隧道和曲线半径数据：TrackData 当前未加载隧道/曲线数据。
可通过 tunnel_seg_ids / curve_seg_radius 参数补充，或使用默认值（无隧道、无曲线）。
"""

from collections import deque
from typing import Optional, Dict, TYPE_CHECKING

from src.common.track_position import TrackPosition, ITrackQuery
from src.track.data import TrackData

if TYPE_CHECKING:
    from src.track.route import Route


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

        # ── 进路状态 ─────────────────────────────────────────
        self._active_route: Optional["Route"] = None

        # ── 主线 segment ID 集合（仅 forward neighbor 链） ──
        self._main_line_seg_ids: set[int] = self._compute_main_line_seg_ids()

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

    # ── 进路管理 ───────────────────────────────────────────────

    def set_active_route(self, route: Optional["Route"]) -> None:
        """设置当前活动进路。

        from_absolute() 在道岔重叠区域优先选择属于该进路的 segment。
        设为 None 时回退到主线启发式。
        """
        self._active_route = route

    def get_active_route(self) -> Optional["Route"]:
        """获取当前活动进路。"""
        return self._active_route

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
        """线路绝对里程 → TrackPosition，带道岔消歧。

        3 层消歧策略：
          1. 活动进路（Route）匹配 → 选属于 route 的候选段
          2. 主线启发式 → 选属于 _main_line_seg_ids 的候选段
          3. 兜底 → 返回第一个候选段（保持原有行为）

        允许第一段出现负 offset（列车尾部可能在线路起点之前）。
        """
        total = self._td.total_length()
        if abs_pos >= total:
            last = max(self._td.segments, key=lambda s: s.abs_start + s.length)
            return TrackPosition(segment_id=last.seg_id, offset=last.length)

        # Step 1: 收集所有覆盖该绝对位置的候选 segment
        candidates = []
        for seg in self._td.segments:
            seg_end = seg.abs_start + seg.length
            if seg.abs_start <= abs_pos < seg_end:
                candidates.append(seg)

        # Step 2: 消歧
        if len(candidates) == 0:
            # 无候选（abs_pos < 0 或落在间隙中）
            pass
        elif len(candidates) == 1:
            # 唯一候选，直接返回
            seg = candidates[0]
            return TrackPosition(segment_id=seg.seg_id,
                                 offset=abs_pos - seg.abs_start)
        else:
            # 多个候选（道岔重叠区域），按优先级选择
            chosen = self._disambiguate_candidates(candidates, abs_pos)
            if chosen is not None:
                return TrackPosition(segment_id=chosen.seg_id,
                                     offset=abs_pos - chosen.abs_start)

        # Step 3: 兜底 — abs_pos < 0 或落在段间隙中
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

    # ── 内部方法 ────────────────────────────────────────────────

    def _compute_main_line_seg_ids(self) -> set[int]:
        """计算主线 segment ID 集合。

        主线 = 所有从"入口段"出发，沿 start_neighbor/end_neighbor 可达的 segment。
        "入口段"定义：至少有一个 forward neighbor，且不在另一个有 forward
        neighbor 的段的 forward 方向汇入点。

        对于简单链式主线（如 demo），这等价于沿 forward 链遍历的全部段。
        对于复杂路网（如数据库的交叉渡线），侧线段（仅被 lateral 引用的段）
        不会出现在主线集合中。

        实现：BFS，从所有有 forward 邻居的段出发，只沿 forward 方向遍历。
        """
        if not self._td.segments:
            return set()

        seg_map = self._td._seg_map

        # 找所有有 forward 邻居的 segment
        forward_segs = set()
        for s in self._td.segments:
            has_fwd = ((s.start_neighbor > 0 and s.start_neighbor != 65535) or
                       (s.end_neighbor > 0 and s.end_neighbor != 65535))
            if has_fwd:
                forward_segs.add(s.seg_id)

        if not forward_segs:
            return {s.seg_id for s in self._td.segments}

        # 入口段：有 forward 邻居，且自身不被其他 forward 邻居所指向
        # （即不在 forward 链的"中间"或"末端"，而是在链的起点）
        # 对于双向链，所有 forward 段都相互引用，所以用另一种方式：
        # 找被 forward 邻居引用次数最少的段作为起点。
        referenced: dict[int, int] = {sid: 0 for sid in forward_segs}
        for s in self._td.segments:
            if s.seg_id not in seg_map:
                continue
            for nid in (s.start_neighbor, s.end_neighbor):
                if nid > 0 and nid != 65535 and nid in referenced:
                    referenced[nid] = referenced.get(nid, 0) + 1

        # 入口 = 有 forward 邻居且被引用次数为 0（或被引用最少的）
        # 对于 demo：seg1 被 seg2 引用 1 次，seg6/seg7 没有 forward 邻居不在 forward_segs 中
        # 对于链式结构，找引用数为 0 的作为入口
        entries = [sid for sid, count in referenced.items() if count == 0]
        if not entries:
            # 无纯入口（可能是环路），取第一个有 forward 邻居的段
            entries = list(forward_segs)[:1]

        # BFS 从所有入口出发，只沿 forward 边
        visited: set[int] = set()
        q = deque(entries)
        visited.update(entries)

        while q:
            cur_id = q.popleft()
            cur = seg_map.get(cur_id)
            if cur is None:
                continue
            for nid in (cur.start_neighbor, cur.end_neighbor):
                if nid <= 0 or nid == 65535:
                    continue
                if nid not in visited and nid in seg_map:
                    visited.add(nid)
                    q.append(nid)

        return visited

    def _disambiguate_candidates(self, candidates, abs_pos):
        """从多个候选 segment 中选择正确的 segment。

        优先级：
          1. 活动进路匹配
          2. 主线启发式
          3. 第一个候选（兜底）
        """
        # 优先级 1: 活动进路
        if self._active_route is not None and not self._active_route.is_auto:
            route_set = self._active_route.seg_id_set
            for seg in candidates:
                if seg.seg_id in route_set:
                    return seg

        # 优先级 2: 主线启发式
        for seg in candidates:
            if seg.seg_id in self._main_line_seg_ids:
                return seg

        # 优先级 3: 兜底
        return candidates[0]
