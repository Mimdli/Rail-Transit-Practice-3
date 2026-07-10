"""基于区段占用和进路锁闭的简化调度联锁。"""

from dataclasses import dataclass
from typing import Iterable, Optional

from src.track.data import TrackData

from .models import TrainRuntime


class BlockOccupancyManager:
    """根据各节车所在区段生成实时占用表。"""

    def __init__(self):
        self._occupied: dict[int, set[str]] = {}

    def update(self, trains: Iterable[TrainRuntime]):
        occupied: dict[int, set[str]] = {}
        for runtime in trains:
            for state in runtime.controller.states:
                occupied.setdefault(state.position.segment_id, set()).add(runtime.train_id)
        self._occupied = occupied

    def owners(self, segment_id: int) -> frozenset[str]:
        return frozenset(self._occupied.get(segment_id, set()))

    def occupied_by_other(self, segment_id: int, train_id: str) -> bool:
        return any(owner != train_id for owner in self._occupied.get(segment_id, set()))

    @property
    def snapshot(self) -> dict[int, frozenset[str]]:
        return {sid: frozenset(owners) for sid, owners in self._occupied.items()}


@dataclass(frozen=True)
class RouteRequestResult:
    granted: bool
    reason: str = ""
    conflicting_train: Optional[str] = None


class InterlockingService:
    """办理、取消和释放列车进路。"""

    def __init__(self, track: TrackData, occupancy: BlockOccupancyManager):
        self.track = track
        self.occupancy = occupancy
        self._locks: dict[int, str] = {}
        self._routes: dict[str, tuple[int, ...]] = {}

    def request_route(self, train_id: str,
                      segment_ids: Iterable[int]) -> RouteRequestResult:
        segments = tuple(dict.fromkeys(int(sid) for sid in segment_ids if sid))
        if not segments:
            return RouteRequestResult(False, "没有可办理的线路区段")

        for segment_id in segments:
            owner = self._locks.get(segment_id)
            if owner is not None and owner != train_id:
                return RouteRequestResult(
                    False, f"区段 LK{segment_id} 已被 {owner} 进路锁闭", owner)
            other_owners = self.occupancy.owners(segment_id) - {train_id}
            if other_owners:
                other = sorted(other_owners)[0]
                return RouteRequestResult(
                    False, f"区段 LK{segment_id} 被 {other} 占用", other)

        self.cancel_route(train_id)
        for segment_id in segments:
            self._locks[segment_id] = train_id
        self._routes[train_id] = segments
        return RouteRequestResult(True)

    def cancel_route(self, train_id: str):
        for segment_id in self._routes.pop(train_id, ()):
            if self._locks.get(segment_id) == train_id:
                self._locks.pop(segment_id, None)

    def route_between(self, start_abs: float, target_abs: float,
                      direction: int) -> tuple[int, ...]:
        """按绝对里程生成区段级进路，优先使用主线段。"""
        low, high = sorted((start_abs, target_abs))
        candidates = []
        for segment in self.track.segments:
            seg_start = segment.abs_start
            seg_end = segment.abs_start + segment.length
            if seg_end < low or seg_start > high:
                continue
            # 有正向邻接的区段视为主线；孤立线路则回退到全部候选。
            is_main = bool(segment.start_neighbor or segment.end_neighbor)
            candidates.append((segment, is_main))
        main = [segment for segment, is_main in candidates if is_main]
        selected = main or [segment for segment, _ in candidates]
        selected.sort(key=lambda segment: segment.abs_start,
                      reverse=direction < 0)
        return tuple(segment.seg_id for segment in selected)

    def locked_by(self, segment_id: int) -> Optional[str]:
        return self._locks.get(segment_id)

    @property
    def locks(self) -> dict[int, str]:
        return dict(self._locks)
