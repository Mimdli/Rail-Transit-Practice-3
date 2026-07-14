"""视景边、内部 Link 公里标和站台标注之间的坐标转换。"""

from __future__ import annotations

import bisect
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from src.common.track_position import TrackPosition
from src.track.data import TrackData
from src.track.link_mainline import LinkCoordinateMapper, load_mainline_links


DATA_PATH = Path(__file__).resolve().parents[2] / "resource" / "vision_alignment.json"


@dataclass(frozen=True)
class VisionEdge:
    """视景系统中的一条有向边。"""

    edge_id: int
    start_m: float
    end_m: float
    length_m: float


@dataclass(frozen=True)
class VisionPlatform:
    """按运行方向区分的视景站台停车范围。"""

    track: int
    direction: str
    station_id: int
    vision_station_id: int
    station_code: str
    station_name: str
    center_m: float
    stop_start_m: float
    stop_end_m: float
    platform_side: str


@dataclass(frozen=True)
class VisionPosition:
    """可直接写入 TCMS2VIEW 的视景边内位置。"""

    direction: str
    edge_id: int
    offset_m: float
    line_m: float


@lru_cache(maxsize=1)
def load_vision_alignment() -> tuple[
        dict[str, tuple[VisionEdge, ...]], tuple[VisionPlatform, ...]]:
    """加载由最新视景标注表固化的边和站台数据。"""
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    edges = {
        direction: tuple(VisionEdge(**item) for item in items)
        for direction, items in payload["tracks"].items()
    }
    platforms = tuple(VisionPlatform(**item) for item in payload["platforms"])
    return edges, platforms


class VisionCoordinateMapper:
    """把项目内部 TrackPosition 映射为视景边号和边内位移。

    两套线路的累计里程存在小幅差异，因此使用首尾点和 13 个站台停车点
    做分段线性校准。协议位置表示车头，因此到站时按运行方向映射到站台
    远端停车标，避免车头停在站台中心而视觉上提前约半个站台长度。
    """

    def __init__(self, track: TrackData, link_source: str = "directions"):
        self.track = track
        self.link_source = link_source
        self.edges, self.platforms = load_vision_alignment()
        self._link_mapper = LinkCoordinateMapper(track, link_source)
        self._anchors = self._build_anchors()

    @staticmethod
    def direction_key(direction: int | str) -> str:
        if isinstance(direction, str):
            value = direction.lower()
            if value in ("up", "down"):
                return value
            raise ValueError(f"未知视景方向: {direction}")
        return "down" if direction >= 0 else "up"

    @property
    def available(self) -> bool:
        """正式线路上下行均完成标定时返回 True。"""
        return all(len(self._anchors.get(key, ())) >= 2 for key in ("up", "down"))

    def _build_anchors(self) -> dict[str, tuple[tuple[float, float], ...]]:
        # 演示线路没有对应的真实视景标注，不做跨数据源猜测。
        if self.link_source != "directions":
            return {}

        links_by_direction = load_mainline_links(self.link_source)
        anchors_by_direction = {}
        for direction in ("up", "down"):
            links = links_by_direction[direction]
            edges = self.edges[direction]
            anchors = [(links[0].start_m, edges[0].start_m)]
            annotations = sorted(
                (item for item in self.platforms if item.direction == direction),
                key=lambda item: item.station_id,
            )
            for annotation in annotations:
                platform = next(
                    (item for item in self.track.platforms
                     if item.station_id == annotation.station_id
                     and item.direction.lower() == direction),
                    None,
                )
                if platform is None:
                    continue
                segment = self.track._seg_map.get(platform.seg_id)
                if segment is None:
                    continue
                offset = max(0.0, min(
                    segment.length, platform.position - segment.abs_start))
                source_m = self._link_mapper.to_link_position(
                    TrackPosition(platform.seg_id, offset))
                if source_m is not None:
                    # 下行沿公里标递增，车头停在站台高里程端；上行相反。
                    head_stop_m = (annotation.stop_end_m
                                   if direction == "down"
                                   else annotation.stop_start_m)
                    anchors.append((source_m, head_stop_m))
            anchors.append((links[-1].end_m, edges[-1].end_m))
            if self._is_strictly_increasing(anchors):
                anchors_by_direction[direction] = tuple(anchors)
        return anchors_by_direction

    @staticmethod
    def _is_strictly_increasing(anchors: list[tuple[float, float]]) -> bool:
        return all(
            left[0] < right[0] and left[1] < right[1]
            for left, right in zip(anchors, anchors[1:])
        )

    def to_vision_line_m(self, position: TrackPosition,
                         direction: int | str) -> Optional[float]:
        """返回校准后的视景全线公里标；非正式主线位置返回 None。"""
        key = self.direction_key(direction)
        anchors = self._anchors.get(key)
        source_m = self._link_mapper.to_link_position(position)
        if not anchors or source_m is None:
            return None

        source_values = [item[0] for item in anchors]
        if source_m <= source_values[0]:
            return anchors[0][1]
        if source_m >= source_values[-1]:
            return anchors[-1][1]
        right_index = bisect.bisect_right(source_values, source_m)
        source_left, vision_left = anchors[right_index - 1]
        source_right, vision_right = anchors[right_index]
        ratio = (source_m - source_left) / (source_right - source_left)
        return vision_left + ratio * (vision_right - vision_left)

    def to_vision_position(self, position: TrackPosition,
                           direction: int | str) -> Optional[VisionPosition]:
        """返回视景边号、边内位移和全线公里标。"""
        key = self.direction_key(direction)
        line_m = self.to_vision_line_m(position, key)
        if line_m is None:
            return None
        edges = self.edges[key]
        edge = next(
            (item for item in edges if item.start_m <= line_m < item.end_m),
            edges[-1] if line_m == edges[-1].end_m else None,
        )
        if edge is None:
            return None
        offset_m = max(0.0, min(edge.length_m, line_m - edge.start_m))
        return VisionPosition(key, edge.edge_id, offset_m, line_m)

    def platform_at(self, position: TrackPosition,
                    direction: int | str) -> Optional[VisionPlatform]:
        """按真实停车边界返回当前位置所属站台。"""
        key = self.direction_key(direction)
        line_m = self.to_vision_line_m(position, key)
        if line_m is None:
            return None
        matches = [
            item for item in self.platforms
            if item.direction == key
            and item.stop_start_m <= line_m <= item.stop_end_m
        ]
        if not matches:
            return None
        return min(matches, key=lambda item: abs(item.center_m - line_m))

    def platform_side_at(self, position: TrackPosition,
                         direction: int | str) -> str:
        platform = self.platform_at(position, direction)
        return "" if platform is None else platform.platform_side
