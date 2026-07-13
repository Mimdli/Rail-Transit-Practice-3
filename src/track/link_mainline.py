"""基于校对后 Link 公里标数据的主线定义。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.common.track_position import TrackPosition
from src.track.data import TrackData


DATA_PATH = Path(__file__).resolve().parents[2] / "resource" / "link_mainline.json"


@dataclass(frozen=True)
class MainlineLink:
    """主线上的一个有序 Link，位置和长度均使用米。"""

    link_id: int
    start_m: float
    end_m: float
    length_m: float


def load_mainline_links(source: str = "directions") -> dict[str, tuple[MainlineLink, ...]]:
    """加载上下行 Link 链，并保持工作簿中的连接次序。

    Args:
        source: 数据源键名，``"directions"``（数据库线路，默认）
                或 ``"demo"``（演示线路）。
    """
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if source not in payload:
        raise KeyError(f"link_mainline.json 中缺少键: {source}（可用: {list(payload)}）")
    return {
        direction: tuple(MainlineLink(**item) for item in items)
        for direction, items in payload[source].items()
    }


def mainline_segment_ids() -> set[int]:
    """返回上下行主线 Link ID 并集（含演示数据）。"""
    payload = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    ids: set[int] = set()
    # 正式线路数据
    for links in payload.get("directions", {}).values():
        for link in links:
            ids.add(link["link_id"])
    # 演示线路数据
    for links in payload.get("demo", {}).values():
        for link in links:
            ids.add(link["link_id"])
    return ids


class LinkCoordinateMapper:
    """把 Seg 内位置转换为线路图唯一使用的 Link 公里标。"""

    def __init__(self, track: TrackData, link_source: str = "directions"):
        segment_lengths = {
            segment.seg_id: segment.length for segment in track.segments
        }
        self._links = {}
        self._directions = {}
        for direction, links in load_mainline_links(link_source).items():
            for link in links:
                if (link.link_id in segment_lengths
                        and abs(segment_lengths[link.link_id]
                                - link.length_m) < 1e-6):
                    self._links[link.link_id] = link
                    self._directions[link.link_id] = direction

    def to_link_position(self, position: TrackPosition) -> float | None:
        """返回 Link 公里标（m）；非主线 Seg 返回 None。"""
        link = self._links.get(position.segment_id)
        if link is None:
            return None
        offset = max(0.0, min(link.length_m, position.offset))
        return link.start_m + offset

    def offset_at(self, segment_id: int, link_position_m: float) -> float | None:
        """把线路图公里标反算为指定 Link 内偏移。"""
        link = self._links.get(segment_id)
        if link is None:
            return None
        return max(0.0, min(link.length_m, link_position_m - link.start_m))

    def link_for_segment(self, segment_id: int) -> MainlineLink | None:
        return self._links.get(segment_id)

    def direction_for_segment(self, segment_id: int) -> str | None:
        return self._directions.get(segment_id)

    def midpoint_for_segment(self, segment_id: int) -> float | None:
        link = self._links.get(segment_id)
        return None if link is None else (link.start_m + link.end_m) / 2.0
