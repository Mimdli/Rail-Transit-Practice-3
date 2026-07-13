"""Route — 列车进路定义与自动路由计算

Route 是一组有序 segment ID 序列，定义了列车从起点到终点的路径。
在道岔处，Route 决定了车辆沿主线还是侧线前进。

自动路由计算：沿主线 forward neighbor 链找到从起点 segment 到
目标 segment 的路径。对复杂路网（如数据库的交叉渡线），后续可扩展
为图搜索算法。
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict

from src.track.data import TrackData


@dataclass
class Route:
    """列车进路 — 有序 segment ID 序列。

    seg_ids 为空列表时表示"自动"模式 —— 由外部逻辑（如 AutoDriveController）
    在设定目标时动态计算实际路线。

    Attributes:
        route_id: 路线编号，0 保留给"自动"模式。
        name: 路线名称（UI 显示用）。
        seg_ids: 有序的 segment ID 列表。
    """
    route_id: int
    name: str
    seg_ids: List[int] = field(default_factory=list)

    # ── 查询方法 ──────────────────────────────────────────────

    def get_next_segment(self, current_seg_id: int) -> Optional[int]:
        """返回当前 segment 的下一个 segment ID。

        在当前 seg_ids 列表中查找 current_seg_id，返回其后一个元素。
        如果 current_seg_id 不在列表中、或是列表最后一个元素，返回 None。

        Args:
            current_seg_id: 当前所在的 segment ID。

        Returns:
            下一个 segment ID，或 None（到达路线终点或不在路线中）。
        """
        try:
            idx = self.seg_ids.index(current_seg_id)
        except ValueError:
            return None
        if idx + 1 < len(self.seg_ids):
            return self.seg_ids[idx + 1]
        return None

    def contains_segment(self, seg_id: int) -> bool:
        """检查 segment 是否属于此路线。"""
        return seg_id in self.seg_ids

    @property
    def seg_id_set(self) -> frozenset:
        """返回 seg_ids 的不可变集合，供 from_absolute O(1) 成员查询。

        每次访问创建新的 frozenset，但 from_absolute 的调用频率
        （每微步每节车）在可接受范围内。
        """
        return frozenset(self.seg_ids)

    @property
    def is_auto(self) -> bool:
        """是否为"自动"模式（seg_ids 为空，由系统动态算路）。"""
        return len(self.seg_ids) == 0

    @property
    def first_seg_id(self) -> Optional[int]:
        """路线的第一个 segment ID。"""
        return self.seg_ids[0] if self.seg_ids else None

    @property
    def last_seg_id(self) -> Optional[int]:
        """路线的最后一个 segment ID。"""
        return self.seg_ids[-1] if self.seg_ids else None


# ═══════════════════════════════════════════════════════════════
# 自动路由计算
# ═══════════════════════════════════════════════════════════════

def compute_mainline_route(
    track_data: TrackData,
    start_seg_id: int,
    target_seg_id: int,
    name: str = "自动路线",
) -> Optional[Route]:
    """沿主线 forward neighbor 链，计算从 start 到 target 的 Route。

    从 start_seg_id 出发，沿 end_neighbor 逐段向前遍历，
    直到到达 target_seg_id，收集沿途所有 seg_id 形成 Route。
    如果 target 不在主线的 forward 链上（例如侧线车站），返回 None。

    Args:
        track_data: 已构建坐标的 TrackData 实例。
        start_seg_id: 起始 segment ID。
        target_seg_id: 目标 segment ID。
        name: 路线名称。

    Returns:
        计算出的 Route，或 None（无法到达）。
    """
    seg_map = track_data._seg_map
    if start_seg_id not in seg_map:
        return None

    # 目标就是起点 → 单段路线
    if start_seg_id == target_seg_id:
        return Route(route_id=-1, name=name, seg_ids=[start_seg_id])

    # 沿 end_neighbor 链遍历
    seg_ids = [start_seg_id]
    visited = {start_seg_id}
    current_id = start_seg_id

    while current_id != target_seg_id:
        seg = seg_map.get(current_id)
        if seg is None:
            return None

        # 优先走 end_neighbor（正向），其次 start_neighbor（可能是反向）
        next_id = seg.end_neighbor if seg.end_neighbor and seg.end_neighbor != 65535 else 0
        if next_id <= 0:
            # 尝试 start_neighbor
            next_id = seg.start_neighbor if seg.start_neighbor and seg.start_neighbor != 65535 else 0

        if next_id <= 0 or next_id in visited:
            return None  # 死路或环路，无法到达 target

        seg_ids.append(next_id)
        visited.add(next_id)
        current_id = next_id

        # 安全上限：避免无限循环（主线不应该有环路）
        if len(seg_ids) > 500:
            return None

    return Route(route_id=-1, name=name, seg_ids=seg_ids)


def compute_mainline_route_to_station(
    track_data: TrackData,
    start_seg_id: int,
    target_station_name: str,
) -> Optional[Route]:
    """计算从 start 到目标车站的主线路由。

    Args:
        track_data: 已构建坐标的 TrackData 实例。
        start_seg_id: 起始 segment ID。
        target_station_name: 目标车站名称。

    Returns:
        计算出的 Route，或 None。
    """
    # 找到目标车站所在的 segment（通过站台关联的 seg_id，避免绝对坐标歧义）
    target_station = track_data._station_map.get(target_station_name)
    if target_station is None:
        return None

    # 优先使用站台的显式 seg_id（database schema 中 platform.seg_id 直接关联段），
    # 避免 track_data.get_seg_id_at(station.position) 在并行链坐标重叠时选错段。
    target_seg_id = 0
    for pid in target_station.platform_ids:
        for p in track_data.platforms:
            if p.platform_id == pid and p.seg_id in track_data._seg_map:
                target_seg_id = p.seg_id
                break
        if target_seg_id:
            break

    # 兜底：无站台关联时用绝对位置查询
    if target_seg_id == 0:
        target_seg_id = track_data.get_seg_id_at(target_station.position)
    if target_seg_id == 0:
        return None

    return compute_mainline_route(
        track_data,
        start_seg_id,
        target_seg_id,
        name=f"自动→{target_station_name}",
    )
