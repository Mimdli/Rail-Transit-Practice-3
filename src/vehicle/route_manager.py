"""RouteManager — 列车进路管理器

独立于驾驶模式，只负责"走哪条路"：
  1. 存储所有可用进路
  2. 管理当前活动进路
  3. 自动算路（沿主线 forward neighbor 链）
  4. 同步活动进路到 TrackDataAdapter

Usage:
    from src.track.adapter import TrackDataAdapter
    adapter = TrackDataAdapter(track_data)
    rm = RouteManager(adapter)
    rm.set_available_routes(routes)
    rm.set_route(auto_route)
    # 设目标后自动算路：
    rm.compute_route_to(target_position, head_position)
"""

from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from src.track.route import Route
    from src.common.track_position import TrackPosition, ITrackQuery


class RouteManager:
    """列车进路管理器 —— 独立于驾驶模式。

    职责：
      1. 存储所有可用进路
      2. 管理当前活动进路
      3. 自动算路（沿主线 forward neighbor 链）
      4. 同步活动进路到 TrackDataAdapter
    """

    def __init__(self, track_adapter: "ITrackQuery"):
        """
        Args:
            track_adapter: 线路数据适配器（用于同步进路和坐标转换）。
        """
        self._track_adapter = track_adapter
        self._all_routes: List["Route"] = []
        self._active_route: Optional["Route"] = None

    # ── 属性 ───────────────────────────────────────────────────

    @property
    def track_adapter(self) -> "ITrackQuery":
        """获取当前线路适配器。"""
        return self._track_adapter

    @track_adapter.setter
    def track_adapter(self, adapter: "ITrackQuery"):
        """更新线路适配器（切换数据源时调用）。"""
        self._track_adapter = adapter

    @property
    def active_route(self) -> Optional["Route"]:
        """获取当前活动进路。"""
        return self._active_route

    @property
    def available_routes(self) -> List["Route"]:
        """获取所有可用进路列表（供 UI 显示）。"""
        return list(self._all_routes)

    # ── 进路设置 ───────────────────────────────────────────────

    def set_route(self, route: "Route"):
        """设置当前进路。

        如果 route.is_auto 为 True，不立即同步到 track adapter，
        由调用者在设定目标后调用 compute_route_to() 自动算路。
        否则立即将进路同步到 track adapter。

        Args:
            route: 要设置的进路。
        """
        self._active_route = route
        if not route.is_auto:
            self._track_adapter.set_active_route(route)

    def set_available_routes(self, routes: List["Route"]):
        """设置可用进路列表。

        如果当前没有活动进路，默认选中第一个"自动"模式进路。

        Args:
            routes: 所有可用进路（含"自动"模式）。
        """
        self._all_routes = list(routes)
        auto_route = next((r for r in routes if r.is_auto), None)
        if auto_route is not None and self._active_route is None:
            self._active_route = auto_route

    # ── 自动算路 ───────────────────────────────────────────────

    def compute_route_to(self, target_position: "TrackPosition",
                         head_position: "TrackPosition") -> Optional["Route"]:
        """自动计算从头车当前位置到目标的进路，并同步到 track adapter。

        沿主线 forward neighbor 链找到从头车所在 segment 到
        目标所在 segment 的路径。

        Args:
            target_position: 目标位置。
            head_position: 头车当前位置。

        Returns:
            计算出的 Route，或 None（无法到达）。
        """
        from src.track.route import compute_mainline_route
        from src.track.adapter import TrackDataAdapter

        if not isinstance(self._track_adapter, TrackDataAdapter):
            return None

        td = self._track_adapter.track_data
        start_seg = head_position.segment_id
        target_seg = target_position.segment_id

        route = compute_mainline_route(td, start_seg, target_seg)
        if route is not None:
            self._track_adapter.set_active_route(route)
        return route

    def compute_route_to_station(self, station_index: int,
                                  head_position: "TrackPosition") -> Optional["Route"]:
        """计算到指定车站的进路。

        Args:
            station_index: 目标车站在 track_data.stations 中的索引。
            head_position: 头车当前位置。

        Returns:
            计算出的 Route，或 None。
        """
        from src.track.route import compute_mainline_route_to_station
        from src.track.adapter import TrackDataAdapter

        if not isinstance(self._track_adapter, TrackDataAdapter):
            return None

        td = self._track_adapter.track_data
        if station_index < 0 or station_index >= len(td.stations):
            return None

        station = td.stations[station_index]
        start_seg = head_position.segment_id

        route = compute_mainline_route_to_station(td, start_seg, station.name)
        if route is not None:
            self._track_adapter.set_active_route(route)
        return route
