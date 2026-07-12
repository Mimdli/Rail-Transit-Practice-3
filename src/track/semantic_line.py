"""线路语义抽象 — 将底层 Seg 拓扑转换为运营可视化对象。"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from src.track.data import TrackData
from src.track.link_mainline import LinkCoordinateMapper, mainline_segment_ids


@dataclass(frozen=True)
class SemanticStation:
    """运营线路图中的车站节点。"""

    station_id: int
    name: str
    position: float
    platform_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class SemanticLink:
    """相邻车站之间的运营区间。"""

    start_station_id: int
    end_station_id: int
    start_pos: float
    end_pos: float
    seg_ids: tuple[int, ...] = ()
    down_seg_ids: tuple[int, ...] = ()
    up_seg_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class SemanticBranch:
    """从主线派生出的辅助线语义。"""

    branch_id: int
    anchor_pos: float
    anchor_station_id: int | None
    role: str
    seg_ids: tuple[int, ...] = ()


@dataclass
class SemanticLineModel:
    """面向运行仿真的线路语义模型。"""

    stations: list[SemanticStation] = field(default_factory=list)
    links: list[SemanticLink] = field(default_factory=list)
    branches: list[SemanticBranch] = field(default_factory=list)
    main_seg_ids: set[int] = field(default_factory=set)
    total_length: float = 0.0


def build_semantic_line(track: TrackData) -> SemanticLineModel:
    """构建第一版语义线路模型。

    当前口径偏保守：车站按绝对里程排序，相邻车站之间的 Seg 视为运营区间；
    带侧向邻接但不属于主区间的 Seg 聚类为辅助线，用于避免直接暴露全部工程区段。
    """
    stations = _build_stations(track)
    configured_main_ids = mainline_segment_ids()
    available_main_ids = configured_main_ids.intersection(track._seg_map)
    links, main_seg_ids = _build_links(track, stations, available_main_ids)
    # 站台区段属于运营主线节点，但不重复计入相邻站间区间。
    platform_ids = {pid for station in stations for pid in station.platform_ids}
    main_seg_ids.update(
        platform.seg_id for platform in track.platforms
        if platform.platform_id in platform_ids or platform.station_id > 0
    )
    branches = _build_branches(track, stations, main_seg_ids)
    return SemanticLineModel(
        stations=stations,
        links=links,
        branches=branches,
        main_seg_ids=main_seg_ids,
        total_length=max(1.0, track.total_length()),
    )


def compute_station_route(track: TrackData, start_seg_id: int,
                          target_station_id: int,
                          direction: int = 1) -> tuple[int, ...]:
    """计算当前位置到目标站同方向站台区段的拓扑路径。"""
    station = next(
        (item for item in track.stations if item.station_id == target_station_id),
        None,
    )
    if station is None:
        return ()
    semantic_station = SemanticStation(
        station.station_id, station.name, station.position,
        tuple(station.platform_ids),
    )
    platform_direction = "down" if direction >= 0 else "up"
    targets = _station_platform_segments(
        track, semantic_station, platform_direction)
    path = _shortest_path(track, {start_seg_id}, targets)
    return tuple(path)


def _build_stations(track: TrackData) -> list[SemanticStation]:
    mapper = LinkCoordinateMapper(track)
    positions = [station.position for station in track.stations]
    duplicate_positions = {
        pos for pos in positions
        if sum(1 for item in positions if abs(item - pos) < 0.01) > 1
    }
    stations = []
    for station in sorted(track.stations, key=lambda s: (s.position, s.station_id)):
        platform_ids = tuple(station.platform_ids)
        if not platform_ids and station.position not in duplicate_positions:
            platform_ids = tuple(
                platform.platform_id for platform in track.platforms
                if abs(platform.position - station.position) < 1.0
            )
        platform_seg_ids = {
            platform.seg_id for platform in track.platforms
            if (platform.platform_id in platform_ids
                or platform.station_id == station.station_id)
        }
        link_positions = [
            position for segment_id in platform_seg_ids
            if (position := mapper.midpoint_for_segment(segment_id)) is not None
        ]
        # 校对后的 Link 公里标优先；演示数据仍使用原车站位置。
        position = (sum(link_positions) / len(link_positions)
                    if link_positions else station.position)
        stations.append(
            SemanticStation(
                station_id=station.station_id,
                name=station.name or f"站{station.station_id}",
                position=position,
                platform_ids=platform_ids,
            )
        )
    return stations


def _build_links(
    track: TrackData,
    stations: list[SemanticStation],
    allowed_seg_ids: set[int] | None = None,
) -> tuple[list[SemanticLink], set[int]]:
    links: list[SemanticLink] = []
    main_seg_ids: set[int] = set()
    if len(stations) < 2:
        return links, main_seg_ids

    for left, right in zip(stations, stations[1:]):
        direction_paths: dict[str, tuple[int, ...]] = {}
        for direction in ("down", "up"):
            starts = _station_platform_segments(track, left, direction)
            targets = _station_platform_segments(track, right, direction)
            path = _shortest_path(track, starts, targets, allowed_seg_ids)
            if path:
                # 目标站台所在区段属于下一站站场，不计入当前站间区间。
                path = path[:-1] or path
            direction_paths[direction] = tuple(dict.fromkeys(path))

        down_seg_ids = direction_paths.get("down", ())
        up_seg_ids = direction_paths.get("up", ())
        seg_ids = tuple(dict.fromkeys(
            segment_id for path in direction_paths.values() for segment_id in path
        ))
        if not seg_ids:
            # 数据关联缺失时才使用里程范围兜底，并排除孤立侧线。
            start = min(left.position, right.position)
            end = max(left.position, right.position)
            seg_ids = tuple(
                segment.seg_id for segment in track.segments
                if (not allowed_seg_ids or segment.seg_id in allowed_seg_ids)
                if (segment.start_neighbor or segment.end_neighbor)
                and _overlaps(
                    segment.abs_start, segment.abs_start + segment.length,
                    start, end)
            )
        main_seg_ids.update(seg_ids)
        links.append(
            SemanticLink(
                start_station_id=left.station_id,
                end_station_id=right.station_id,
                start_pos=left.position,
                end_pos=right.position,
                seg_ids=seg_ids,
                down_seg_ids=down_seg_ids,
                up_seg_ids=up_seg_ids,
            )
        )
    return links, main_seg_ids


def _build_branches(
    track: TrackData,
    stations: list[SemanticStation],
    main_seg_ids: set[int],
) -> list[SemanticBranch]:
    branch_candidates = {
        segment.seg_id: segment for segment in track.segments
        if segment.seg_id not in main_seg_ids
    }
    adjacency = _segment_adjacency(track)
    grouped: list[list] = []
    remaining = set(branch_candidates)
    while remaining:
        root = remaining.pop()
        component = [branch_candidates[root]]
        stack = [root]
        while stack:
            current = stack.pop()
            for neighbor, _penalty in adjacency.get(current, ()):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.append(branch_candidates[neighbor])
                    stack.append(neighbor)
        grouped.append(component)

    grouped.sort(key=lambda group: min(segment.abs_start for segment in group))

    branches: list[SemanticBranch] = []
    for idx, group in enumerate(grouped, start=1):
        anchor = min(seg.abs_start for seg in group)
        nearest_station = _nearest_station(stations, anchor)
        branches.append(
            SemanticBranch(
                branch_id=idx,
                anchor_pos=anchor,
                anchor_station_id=nearest_station.station_id if nearest_station else None,
                role=_infer_branch_role(group, nearest_station, anchor),
                seg_ids=tuple(seg.seg_id for seg in group),
            )
        )
    return branches


def _station_platform_segments(track: TrackData, station: SemanticStation,
                               direction: str) -> set[int]:
    platform_ids = set(station.platform_ids)
    matches = {
        platform.seg_id for platform in track.platforms
        if (platform.platform_id in platform_ids
            or platform.station_id == station.station_id
            or platform.station_name == station.name)
        and platform.direction == direction
    }
    if matches:
        return matches
    return {
        platform.seg_id for platform in track.platforms
        if (platform.platform_id in platform_ids
            or platform.station_id == station.station_id
            or platform.station_name == station.name)
    }


def _segment_adjacency(track: TrackData) -> dict[int, list[tuple[int, float]]]:
    """构建无向拓扑；侧向道岔增加代价，避免主线误走折返线。"""
    adjacency: dict[int, list[tuple[int, float]]] = {
        segment.seg_id: [] for segment in track.segments
    }
    valid_ids = set(adjacency)
    for segment in track.segments:
        for neighbor in (segment.start_neighbor, segment.end_neighbor):
            if _valid_neighbor(neighbor) and neighbor in valid_ids:
                adjacency[segment.seg_id].append((neighbor, 0.0))
                adjacency[neighbor].append((segment.seg_id, 0.0))
        for neighbor in (segment.start_lateral, segment.end_lateral):
            if _valid_neighbor(neighbor) and neighbor in valid_ids:
                adjacency[segment.seg_id].append((neighbor, 500.0))
                adjacency[neighbor].append((segment.seg_id, 500.0))
    return adjacency


def _shortest_path(track: TrackData, starts: set[int],
                   targets: set[int],
                   allowed_seg_ids: set[int] | None = None) -> list[int]:
    if not starts or not targets:
        return []
    adjacency = _segment_adjacency(track)
    if allowed_seg_ids:
        # 站台段可能位于正线边界；允许起终点接入，但中间只走校对后的 Link 主线。
        allowed = allowed_seg_ids | starts | targets
        adjacency = {
            segment_id: [item for item in neighbors if item[0] in allowed]
            for segment_id, neighbors in adjacency.items()
            if segment_id in allowed
        }
    lengths = {segment.seg_id: segment.length for segment in track.segments}
    queue = []
    best: dict[int, float] = {}
    previous: dict[int, int] = {}
    for start in starts:
        if start in adjacency:
            best[start] = 0.0
            heapq.heappush(queue, (0.0, start))

    target = None
    while queue:
        cost, current = heapq.heappop(queue)
        if cost != best.get(current):
            continue
        if current in targets:
            target = current
            break
        for neighbor, penalty in adjacency.get(current, ()):
            new_cost = cost + lengths.get(neighbor, 1.0) + penalty
            if new_cost < best.get(neighbor, float("inf")):
                best[neighbor] = new_cost
                previous[neighbor] = current
                heapq.heappush(queue, (new_cost, neighbor))
    if target is None:
        return []

    path = [target]
    while path[-1] not in starts:
        parent = previous.get(path[-1])
        if parent is None:
            return []
        path.append(parent)
    path.reverse()
    return path


def _infer_branch_role(group: list, station: SemanticStation | None, anchor: float) -> str:
    """给分支一个面向展示的粗分类。"""
    seg_count = len(group)
    lateral_count = sum(
        1 for seg in group
        if _valid_neighbor(seg.start_lateral) or _valid_neighbor(seg.end_lateral)
    )
    if station and abs(station.position - anchor) < 260:
        return "站场/折返线" if lateral_count else "站台辅助线"
    if seg_count >= 6 or lateral_count >= 2:
        return "联络/道岔群"
    return "辅助线"


def _nearest_station(stations: list[SemanticStation], position: float) -> SemanticStation | None:
    if not stations:
        return None
    return min(stations, key=lambda station: abs(station.position - position))


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and a_end > b_start


def _valid_neighbor(seg_id: int) -> bool:
    return seg_id is not None and seg_id > 0 and seg_id != 65535
