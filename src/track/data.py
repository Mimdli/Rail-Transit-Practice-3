"""轨道线路数据结构 — 基于真实线路数据的简化模型

数据来源: 线路数据(1).xls (33个Sheet)
本模块使用其中的: 车站表, 站台表, Seg表, 静态限速表, 坡度表

坐标构建说明:
  Seg 表通过正向邻居 (start/end_neighbor) 和侧向邻居 (start/end_lateral)
  构成线路拓扑。build_coordinates() 采用 BFS 图遍历，确保主线和道岔分支
  都能获得绝对坐标。分支段坐标从分支点所在父段的终点开始计算。
"""

from collections import deque
from dataclasses import dataclass, field
from typing import List, Optional, Dict

# 真实线路坡度一般不超过 ±40‰；源 Excel 偶发将 ‰ 放大 10 倍（如 300 表示 30‰）
_MAX_PHYSICAL_GRADIENT = 40.0


def normalize_gradient_value(gradient: float) -> float:
    """将坡度值规范到仿真可用的物理范围 (‰)。"""
    g = float(gradient)
    if abs(g) > 100.0:
        g /= 10.0
    if abs(g) > _MAX_PHYSICAL_GRADIENT:
        g = max(-_MAX_PHYSICAL_GRADIENT, min(_MAX_PHYSICAL_GRADIENT, g))
    return g


@dataclass
class Station:
    """车站"""
    station_id: int
    name: str
    position: float              # 绝对位置 (m)
    platform_ids: List[int] = field(default_factory=list)


@dataclass
class Platform:
    """站台"""
    platform_id: int
    position: float              # 绝对位置 (m)
    seg_id: int
    direction: str               # "up" / "down"
    station_name: str = ""


@dataclass
class Segment:
    """线路区段"""
    seg_id: int
    length: float                # 长度 (m)
    start_neighbor: int          # 起点正向相邻SegID
    end_neighbor: int            # 终点正向相邻SegID
    start_lateral: int = 0       # 起点侧向相邻点SegID (道岔分支)
    end_lateral: int = 0         # 终点侧向相邻点SegID (道岔分支)
    abs_start: float = 0.0       # 绝对起点位置 (m)，由构建时计算


@dataclass
class SpeedLimit:
    """限速区段"""
    seg_id: int
    start_offset: float          # 区段内偏移 (m)
    end_offset: float
    speed_limit: float           # 限速值 (m/s)
    abs_start: float = 0.0       # 绝对起点位置 (m)，加载时计算
    abs_end: float = 0.0         # 绝对终点位置 (m)


@dataclass
class Gradient:
    """坡度区段"""
    seg_id: int
    start_offset: float
    end_offset: float
    gradient: float              # 坡度值 (‰)
    direction: str = ""          # 倾斜方向
    abs_start: float = 0.0
    abs_end: float = 0.0


@dataclass
class Signal:
    """信号机"""
    signal_id: str
    position: float = 0.0         # 绝对位置 (m)，由 build_coordinates 计算
    direction: str = ""
    seg_id: int = 0               # 所在区段
    offset: float = 0.0           # 区段内偏移 (m)


class TrackData:
    """线路数据集合"""

    def __init__(self):
        self.stations: List[Station] = []
        self.platforms: List[Platform] = []
        self.segments: List[Segment] = []
        self.speed_limits: List[SpeedLimit] = []
        self.gradients: List[Gradient] = []
        self.signals: List[Signal] = []

        # 索引
        self._seg_map: Dict[int, Segment] = {}
        self._station_map: Dict[str, Station] = {}

    # ---- 构建辅助 ----

    def build_coordinates(self):
        """构建线段坐标：BFS 图遍历，支持主线和道岔分支

        遍历策略:
          1. 找到根段（没有其他段指向它的 seg_id 作为 forward 邻居）
          2. BFS 队列，从根段出发
          3. 对每个段，检查 forward/lateral 邻居，未访问的入队
          4. 分支段坐标 = 父段.abs_start + 父段.length
        """
        self._seg_map = {s.seg_id: s for s in self.segments}
        if not self.segments:
            return

        # 收集所有被引用的 seg_id (作为 forward 邻居)
        referenced = set()
        for s in self.segments:
            if s.start_neighbor > 0 and s.start_neighbor != 65535:
                referenced.add(s.start_neighbor)
            if s.end_neighbor > 0 and s.end_neighbor != 65535:
                referenced.add(s.end_neighbor)
            if s.start_lateral > 0 and s.start_lateral != 65535:
                referenced.add(s.start_lateral)
            if s.end_lateral > 0 and s.end_lateral != 65535:
                referenced.add(s.end_lateral)

        # 找到根段：自身存在 且 不被任何 forward 引用
        root_id = None
        for s in self.segments:
            if s.seg_id not in referenced:
                root_id = s.seg_id
                break
        if root_id is None:
            root_id = self.segments[0].seg_id

        # BFS 遍历
        visited = set()
        q = deque()
        q.append(root_id)
        visited.add(root_id)
        self._seg_map[root_id].abs_start = 0.0

        while q:
            cur_id = q.popleft()
            cur = self._seg_map[cur_id]
            child_pos = cur.abs_start + cur.length

            # 检查四个方向的邻居
            for nid in (cur.start_neighbor, cur.end_neighbor,
                        cur.start_lateral, cur.end_lateral):
                if nid <= 0 or nid == 65535:
                    continue
                if nid in visited or nid not in self._seg_map:
                    continue
                visited.add(nid)
                self._seg_map[nid].abs_start = child_pos
                q.append(nid)

        # 映射限速、坡度和信号到绝对位置
        for sl in self.speed_limits:
            seg = self._seg_map.get(sl.seg_id)
            if seg:
                sl.abs_start = seg.abs_start + sl.start_offset
                sl.abs_end = seg.abs_start + sl.end_offset

        for g in self.gradients:
            seg = self._seg_map.get(g.seg_id)
            if seg:
                g.abs_start = seg.abs_start + g.start_offset
                g.abs_end = seg.abs_start + g.end_offset

        for sig in self.signals:
            seg = self._seg_map.get(sig.seg_id)
            if seg:
                sig.position = seg.abs_start + sig.offset

        self._station_map = {s.name: s for s in self.stations}

    # ---- 查询方法 ----

    def get_speed_limit_at(self, position: float) -> float:
        """获取指定位置 (m) 的限速值 (m/s)"""
        for sl in self.speed_limits:
            if sl.abs_start <= position <= sl.abs_end:
                return sl.speed_limit
        return 22.0  # 默认限速

    def get_gradient_at(self, position: float, seg_id: int = 0) -> float:
        """获取指定位置 (m) 的坡度值 (‰)。

        seg_id 非 0 时优先匹配该区段上的坡度记录，避免岔口共线里程误匹配。
        """
        matched = []
        for g in self.gradients:
            if g.abs_start <= position <= g.abs_end:
                if seg_id == 0 or g.seg_id == seg_id:
                    matched.append(g)
        if not matched and seg_id:
            for g in self.gradients:
                if g.abs_start <= position <= g.abs_end:
                    matched.append(g)
        if matched:
            # 共线重叠时取 seg_id 最小者（与 from_absolute 主线优先一致）
            g = min(matched, key=lambda x: x.seg_id)
            return normalize_gradient_value(g.gradient)
        return 0.0

    def get_station_at(self, position: float, threshold: float = 50.0) -> Optional[Station]:
        """获取指定位置的车站"""
        for station in self.stations:
            if abs(station.position - position) < threshold:
                return station
        return None

    def get_platform_side_at(self, position: float) -> str:
        """获取指定位置的站台侧 ('left' / 'right' / '')"""
        for p in self.platforms:
            if abs(p.position - position) < 60:
                return "right" if p.direction == "down" else "left"
        return ""

    def get_nearest_station_ahead(self, position: float) -> Optional[Station]:
        """获取前方最近的车站"""
        ahead = [s for s in self.stations if s.position > position]
        if not ahead:
            return None
        return min(ahead, key=lambda s: s.position - position)

    def total_length(self) -> float:
        """获取线路总长 (m)"""
        if not self.segments:
            return 0.0
        last = max(self.segments, key=lambda s: s.abs_start + s.length)
        return last.abs_start + last.length

    def get_seg_id_at(self, position: float) -> int:
        """获取指定位置所在的 Seg ID"""
        for s in self.segments:
            if s.abs_start <= position < s.abs_start + s.length:
                return s.seg_id
        return 0
