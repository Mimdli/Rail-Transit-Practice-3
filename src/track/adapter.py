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
from typing import Optional, Dict, List, Set, TYPE_CHECKING

from src.common.track_position import TrackPosition, ITrackQuery
from src.track.data import TrackData

if TYPE_CHECKING:
    from src.track.route import Route

# 共线岔点处区段端点里程允许的最大间隙 (m)
_OVERLAP_JUNCTION_TOL = 2.0


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
        self._fork_routes: Dict[int, int] = {}

        # ── 主线 segment ID 集合（仅 forward neighbor 链） ──
        self._main_line_seg_ids: set[int] = self._compute_main_line_seg_ids()

    # ── 岔口路由（演示侧线走行）──────────────────────────────

    def set_fork_route(self, at_seg_id: int, next_seg_id: int) -> None:
        """指定列车离开 at_seg_id 终点时进入的相邻区段。"""
        self._fork_routes[at_seg_id] = next_seg_id

    def use_lateral_fork(self, at_seg_id: int) -> bool:
        """在 at_seg_id 终点优先走侧向分支。"""
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
        """沿区段拓扑推进，支持跨段、道岔与线路末端共线接续。"""
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
        if self._active_route is not None and not self._active_route.is_auto:
            route_set = self._active_route.seg_id_set
            routed = [n for n in cands if n in route_set]
            if routed:
                return routed
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

    def from_absolute(self, abs_pos: float, hint_seg_id: Optional[int] = None) -> TrackPosition:
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
            chosen = self._disambiguate_candidates(candidates, abs_pos, hint_seg_id)
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

    def _disambiguate_candidates(self, candidates, abs_pos, hint_seg_id: Optional[int] = None):
        """从多个候选 segment 中选择正确的 segment。

        优先级：
          1. 活动进路匹配
          2. 同一线路偏好（同链段）
          3. 同链间隙桥接（同链上绝对位置最近的段）
          4. 主线兜底（选绝对位置最接近的候选）
          5. 第一个候选（兜底）
        """
        # 优先级 1: 活动进路
        if self._active_route is not None and not self._active_route.is_auto:
            route_set = self._active_route.seg_id_set
            for seg in candidates:
                if seg.seg_id in route_set:
                    return seg

        # 收集主线候选
        main_candidates = [seg for seg in candidates
                           if seg.seg_id in self._main_line_seg_ids]
        if not main_candidates:
            return candidates[0]

        # 构建 hint_seg_id 所在链的 ID 集合
        chain_ids: set[int] = set()
        if hint_seg_id is not None:
            last = self._td._seg_map.get(hint_seg_id)
            if last is not None:
                chain_ids = {last.seg_id}
                sid = last.end_neighbor
                while sid > 0 and sid != 65535 and sid in self._td._seg_map:
                    chain_ids.add(sid)
                    sid = self._td._seg_map[sid].end_neighbor
                sid = last.start_neighbor
                while sid > 0 and sid != 65535 and sid in self._td._seg_map:
                    chain_ids.add(sid)
                    sid = self._td._seg_map[sid].start_neighbor

        # 优先级 2: 同链匹配 — 主线候选在同链上
        for seg in main_candidates:
            if seg.seg_id in chain_ids:
                return seg

        # 优先级 3: 同链间隙桥接 — 在同链上找绝对位置最接近的段
        # （处理源数据中线路有微小间隙的情况，避免跳线到另一条线）
        if chain_ids:
            best_on_chain = None
            best_dist = float('inf')
            for sid in chain_ids:
                seg = self._td._seg_map.get(sid)
                if seg is None:
                    continue
                mid = seg.abs_start + seg.length / 2
                dist = abs(abs_pos - mid)
                if dist < best_dist:
                    best_dist = dist
                    best_on_chain = seg
            if best_on_chain is not None:
                return best_on_chain

        # 优先级 4: 跨线兜底 — 选绝对位置最接近的候选
        best = min(main_candidates,
                   key=lambda s: abs(abs_pos - (s.abs_start + s.length / 2)))
        return best
