"""线路语义抽象 — 将底层 Seg 拓扑转换为运营可视化对象。"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.track.data import TrackData


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
    links, main_seg_ids = _build_links(track, stations)
    branches = _build_branches(track, stations, main_seg_ids)
    return SemanticLineModel(
        stations=stations,
        links=links,
        branches=branches,
        main_seg_ids=main_seg_ids,
        total_length=max(1.0, track.total_length()),
    )


def _build_stations(track: TrackData) -> list[SemanticStation]:
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
        stations.append(
            SemanticStation(
                station_id=station.station_id,
                name=station.name or f"站{station.station_id}",
                position=station.position,
                platform_ids=platform_ids,
            )
        )
    return stations


def _build_links(track: TrackData, stations: list[SemanticStation]) -> tuple[list[SemanticLink], set[int]]:
    links: list[SemanticLink] = []
    main_seg_ids: set[int] = set()
    if len(stations) < 2:
        return links, main_seg_ids

    segments = sorted(track.segments, key=lambda s: (s.abs_start, s.seg_id))
    for left, right in zip(stations, stations[1:]):
        start = min(left.position, right.position)
        end = max(left.position, right.position)
        seg_ids = tuple(
            seg.seg_id for seg in segments
            if _overlaps(seg.abs_start, seg.abs_start + seg.length, start, end)
        )
        main_seg_ids.update(seg_ids)
        links.append(
            SemanticLink(
                start_station_id=left.station_id,
                end_station_id=right.station_id,
                start_pos=left.position,
                end_pos=right.position,
                seg_ids=seg_ids,
            )
        )
    return links, main_seg_ids


def _build_branches(
    track: TrackData,
    stations: list[SemanticStation],
    main_seg_ids: set[int],
) -> list[SemanticBranch]:
    branch_candidates = []
    for seg in sorted(track.segments, key=lambda s: (s.abs_start, s.seg_id)):
        has_lateral = _valid_neighbor(seg.start_lateral) or _valid_neighbor(seg.end_lateral)
        if not has_lateral and seg.seg_id in main_seg_ids:
            continue
        if has_lateral or seg.seg_id not in main_seg_ids:
            branch_candidates.append(seg)

    branches: list[SemanticBranch] = []
    grouped: list[list] = []
    for seg in branch_candidates:
        if grouped and abs(seg.abs_start - grouped[-1][-1].abs_start) < 180:
            grouped[-1].append(seg)
        else:
            grouped.append([seg])

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
