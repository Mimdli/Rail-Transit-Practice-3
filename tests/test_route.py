"""测试 Route 进路选择机制。

覆盖：
  - Route 基本操作
  - 主线自动路由计算
  - from_absolute() 道岔消歧（有/无进路）
  - advance_position() 沿进路正确跨段
  - 主线启发式（无进路时默认走主线的向后兼容）
"""

import pytest
import sys
import os

# Ensure src is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.track.route import (
    Route,
    compute_mainline_route,
    compute_mainline_route_to_station,
)
from src.track.data import TrackData, Segment, Station
from src.track.adapter import TrackDataAdapter
from src.common.track_position import TrackPosition


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def demo_track():
    """创建 demo TrackData（与 loader.py 中 load_demo_data 相同的结构）。

    主线 4 段 + 4 条道岔侧线，主线总长约 1000m。
    """
    td = TrackData()
    td.segments = [
        Segment(1, 250.0, 0, 2, end_lateral=5),
        Segment(2, 250.0, 1, 3, end_lateral=6),
        Segment(3, 250.0, 2, 4, start_lateral=7),
        Segment(4, 250.0, 3, 0, end_lateral=8),
        Segment(5, 150.0, 0, 0),
        Segment(6, 150.0, 0, 0),
        Segment(7, 120.0, 0, 0),
        Segment(8, 120.0, 0, 0),
    ]
    td.stations = [
        Station(1, "站A", 0.0, [1, 2]),
        Station(2, "站B", 250.0, [3, 4]),
        Station(3, "站C", 500.0, [5, 6]),
        Station(4, "站D", 750.0, [7, 8]),
    ]
    td.build_coordinates()
    return td


@pytest.fixture
def adapter(demo_track):
    """创建 TrackDataAdapter。"""
    return TrackDataAdapter(demo_track)


@pytest.fixture
def mainline_route():
    """主线全程进路。"""
    return Route(1, "主线全程", [1, 2, 3, 4])


@pytest.fixture
def siding_route():
    """侧线进路：从 seg1 转入 seg5。"""
    return Route(2, "站A→侧线1", [1, 5])


@pytest.fixture
def siding_route_seg3():
    """侧线进路：从 seg2 末转入 seg7（seg3 的 start_lateral 道岔）。

    seg7 是 seg3.start_lateral，在 seg2→seg3 边界（500m）处岔出。
    正确进路是 [2, 7]，从 seg2 直接转入 seg7。
    """
    return Route(4, "站C→侧线3", [2, 7])


# ═══════════════════════════════════════════════════════════════
# Route 基本操作
# ═══════════════════════════════════════════════════════════════

class TestRouteBasics:
    """Route 数据类基本操作测试。"""

    def test_get_next_segment(self, mainline_route):
        assert mainline_route.get_next_segment(1) == 2
        assert mainline_route.get_next_segment(2) == 3
        assert mainline_route.get_next_segment(4) is None  # 最后一个
        assert mainline_route.get_next_segment(99) is None  # 不在路线中

    def test_contains_segment(self, mainline_route):
        assert mainline_route.contains_segment(1) is True
        assert mainline_route.contains_segment(3) is True
        assert mainline_route.contains_segment(5) is False  # 侧线
        assert mainline_route.contains_segment(99) is False

    def test_seg_id_set(self, mainline_route):
        s = mainline_route.seg_id_set
        assert isinstance(s, frozenset)
        assert 1 in s
        assert 5 not in s

    def test_is_auto(self):
        auto_route = Route(0, "自动", [])
        assert auto_route.is_auto is True
        normal_route = Route(1, "主线", [1, 2, 3])
        assert normal_route.is_auto is False

    def test_first_last_seg_id(self, mainline_route):
        assert mainline_route.first_seg_id == 1
        assert mainline_route.last_seg_id == 4

    def test_auto_route_first_last(self):
        auto = Route(0, "自动", [])
        assert auto.first_seg_id is None
        assert auto.last_seg_id is None


# ═══════════════════════════════════════════════════════════════
# 自动路由计算
# ═══════════════════════════════════════════════════════════════

class TestComputeMainlineRoute:
    """主线自动路由计算测试。"""

    def test_simple_path(self, demo_track):
        route = compute_mainline_route(demo_track, 1, 4)
        assert route is not None
        assert route.seg_ids == [1, 2, 3, 4]

    def test_partial_path(self, demo_track):
        route = compute_mainline_route(demo_track, 2, 4)
        assert route is not None
        assert route.seg_ids == [2, 3, 4]

    def test_same_segment(self, demo_track):
        route = compute_mainline_route(demo_track, 3, 3)
        assert route is not None
        assert route.seg_ids == [3]

    def test_start_not_found(self, demo_track):
        route = compute_mainline_route(demo_track, 999, 5)
        assert route is None

    def test_target_not_on_mainline(self, demo_track):
        # seg6 是侧线，不在主线上
        route = compute_mainline_route(demo_track, 1, 6)
        assert route is None

    def test_to_station(self, demo_track):
        route = compute_mainline_route_to_station(demo_track, 1, "站D")
        assert route is not None
        # 站D 在 seg4 上
        assert route.seg_ids[-1] == 4

    def test_to_station_not_found(self, demo_track):
        route = compute_mainline_route_to_station(demo_track, 1, "NONEXIST")
        assert route is None


# ═══════════════════════════════════════════════════════════════
# from_absolute() 道岔消歧
# ═══════════════════════════════════════════════════════════════

class TestFromAbsoluteDisambiguation:
    """from_absolute() 在道岔重叠区域的消歧测试。"""

    def test_no_overlap_returns_unique(self, adapter):
        """非道岔区域（无重叠）→ 返回唯一候选。"""
        # seg2 后半段位置：只被 seg2 覆盖（seg2: 250-500m，seg5 只到 400m）
        pos = adapter.from_absolute(450.0)
        assert pos.segment_id == 2

    def test_turnout_with_route_mainline(self, adapter, mainline_route):
        """道岔处有主线进路 → 返回主线 segment。"""
        adapter.set_active_route(mainline_route)
        # seg1 终点 = 250m，seg2（主线）和 seg5（侧线）都从 250m 开始
        pos = adapter.from_absolute(300.0)
        assert pos.segment_id == 2  # 主线

    def test_turnout_with_route_siding(self, adapter, siding_route):
        """道岔处有侧线进路 → 返回侧线 segment。"""
        adapter.set_active_route(siding_route)
        # seg1 终点 = 250m，侧线进路指向 seg5
        pos = adapter.from_absolute(300.0)
        assert pos.segment_id == 5  # 侧线

    def test_turnout_without_route_defaults_mainline(self, adapter):
        """无进路时 → 主线启发式，返回主线 segment。"""
        adapter.set_active_route(None)
        pos = adapter.from_absolute(300.0)
        assert pos.segment_id == 2  # 主线（默认）

    def test_turnout_auto_route_defaults_mainline(self, adapter):
        """自动进路（seg_ids=[]）时 → 主线启发式。"""
        adapter.set_active_route(Route(0, "自动", []))
        pos = adapter.from_absolute(300.0)
        assert pos.segment_id == 2  # 主线（自动进路不参与消歧）

    def test_turnout_at_seg3_start(self, adapter):
        """seg3 起点处（500m）的 start_lateral 道岔消歧。"""
        # seg3.start_lateral=7，seg2 终点 = 500m
        # seg3 和 seg7 都从 500m 开始
        pos = adapter.from_absolute(550.0)
        # 无进路 → 主线
        assert pos.segment_id == 3

    def test_turnout_at_seg3_start_with_siding_route(self, adapter, siding_route_seg3):
        """seg3 起点处有侧线进路 → 返回侧线 seg7。"""
        adapter.set_active_route(siding_route_seg3)
        pos = adapter.from_absolute(550.0)
        assert pos.segment_id == 7  # 侧线

    def test_negative_position(self, adapter):
        """负位置 → 归入根段，允许负 offset。"""
        pos = adapter.from_absolute(-50.0)
        assert pos.segment_id == 1
        assert pos.offset == -50.0

    def test_beyond_end(self, adapter):
        """超出线路终点 → 返回最后一段终点。"""
        total = adapter.total_length()
        pos = adapter.from_absolute(total + 100)
        # 最后一段是 seg8（abs_start+length=1120 最大）
        assert pos.segment_id == 8
        assert pos.offset == 120.0  # seg8 长度 120m


# ═══════════════════════════════════════════════════════════════
# advance_position() 沿进路推进
# ═══════════════════════════════════════════════════════════════

class TestAdvancePositionWithRoute:
    """advance_position() 沿进路正确跨段推进测试。"""

    def test_advance_on_mainline(self, adapter, mainline_route):
        """沿主线进路推进 → 跨段时正确进入主线下一段。"""
        adapter.set_active_route(mainline_route)
        # 从 seg1 接近终点（250m）开始
        pos = TrackPosition(segment_id=1, offset=240.0)
        new_pos = adapter.advance_position(pos, 30.0)
        # 应进入 seg2（主线），而非 seg5（侧线）
        assert new_pos.segment_id == 2
        assert abs(new_pos.offset - 20.0) < 0.01  # 240+30-250=20

    def test_advance_onto_siding(self, adapter, siding_route):
        """沿侧线进路推进 → 跨段时正确进入侧线。"""
        adapter.set_active_route(siding_route)
        pos = TrackPosition(segment_id=1, offset=240.0)
        new_pos = adapter.advance_position(pos, 30.0)
        # 应进入 seg5（侧线）
        assert new_pos.segment_id == 5
        assert abs(new_pos.offset - 20.0) < 0.01

    def test_advance_no_route_defaults_mainline(self, adapter):
        """无进路 → 默认走主线。"""
        adapter.set_active_route(None)
        pos = TrackPosition(segment_id=1, offset=240.0)
        new_pos = adapter.advance_position(pos, 30.0)
        assert new_pos.segment_id == 2  # 主线

    def test_advance_multiple_cars_cross_turnout(self, adapter, siding_route):
        """多节车厢跨道岔：头车进侧线，尾车仍在主线。"""
        adapter.set_active_route(siding_route)

        # 头车：在 seg1 接近终点
        head_pos = TrackPosition(segment_id=1, offset=240.0)
        head_new = adapter.advance_position(head_pos, 30.0)
        assert head_new.segment_id == 5  # 侧线

        # 尾车：还在 seg1 中间（未到道岔点 250m）
        tail_pos = TrackPosition(segment_id=1, offset=200.0)
        tail_new = adapter.advance_position(tail_pos, 30.0)
        assert tail_new.segment_id == 1  # 还在 seg1
        assert abs(tail_new.offset - 230.0) < 0.01

    def test_advance_through_start_lateral_turnout(self, adapter):
        """通过 start_lateral 道岔（seg3 起点→seg7，进路 [2, 7]）。"""
        route = Route(4, "站C→侧线3", [2, 7])
        adapter.set_active_route(route)
        # 从 seg2 接近 seg3 起点（500m），应转入 seg7
        pos = TrackPosition(segment_id=2, offset=240.0)
        new_pos = adapter.advance_position(pos, 30.0)
        # 应进入 seg7（侧线），而非 seg3（主线）
        assert new_pos.segment_id == 7
        assert abs(new_pos.offset - 20.0) < 0.01  # 240+30-250=20 (into seg7)

    def test_advance_negative(self, adapter):
        """反向推进（后退）。"""
        adapter.set_active_route(None)
        pos = TrackPosition(segment_id=2, offset=10.0)
        new_pos = adapter.advance_position(pos, -20.0)
        # 应退入 seg1
        assert new_pos.segment_id == 1
        assert abs(new_pos.offset - 240.0) < 0.01  # 250-20+10=240


# ═══════════════════════════════════════════════════════════════
# ITrackQuery route 接口
# ═══════════════════════════════════════════════════════════════

class TestRouteInterface:
    """ITrackQuery 进路接口测试。"""

    def test_set_and_get_route(self, adapter, mainline_route):
        adapter.set_active_route(mainline_route)
        assert adapter.get_active_route() is mainline_route

    def test_set_none_route(self, adapter):
        adapter.set_active_route(None)
        assert adapter.get_active_route() is None

    def test_mock_track_query_route(self):
        """MockTrackQuery 的进路方法应为空操作。"""
        from src.common.track_position import MockTrackQuery
        mock = MockTrackQuery()
        mock.set_active_route(Route(1, "test", [1, 2]))
        assert mock.get_active_route() is None  # 始终返回 None


# ═══════════════════════════════════════════════════════════════
# 主线启发式（_main_line_seg_ids）
# ═══════════════════════════════════════════════════════════════

class TestMainLineHeuristic:
    """主线 segment ID 集合计算测试。"""

    def test_main_line_includes_all_forward_segments(self, adapter):
        """主线应包含所有 forward neighbor 链上的 segment。"""
        ml = adapter._main_line_seg_ids
        assert 1 in ml
        assert 2 in ml
        assert 3 in ml
        assert 4 in ml

    def test_branch_segments_not_in_main_line(self, adapter):
        """侧线 segment 不应在主线上。"""
        ml = adapter._main_line_seg_ids
        assert 5 not in ml  # seg1 的侧线
        assert 7 not in ml  # seg3 的侧线

    def test_disambiguate_candidates_prefers_mainline(self, adapter):
        """消歧函数：无进路时优先选主线。"""
        from src.track.data import Segment
        # 模拟道岔重叠：两个候选，一个主线一个侧线
        main_seg = Segment(2, 250.0, 1, 3, abs_start=250.0)
        branch_seg = Segment(6, 150.0, 0, 0, abs_start=250.0)
        # main_seg 在 _main_line_seg_ids 中（seg_id=2），branch_seg 不在
        chosen = adapter._disambiguate_candidates([branch_seg, main_seg], 350.0)
        assert chosen is not None
        assert chosen.seg_id == 2  # 优先主线

    def test_disambiguate_candidates_uses_route_first(self, adapter, siding_route):
        """消歧函数：有进路时优先选进路段。"""
        from src.track.data import Segment
        adapter.set_active_route(siding_route)
        main_seg = Segment(2, 250.0, 1, 3, abs_start=250.0)
        branch_seg = Segment(5, 150.0, 0, 0, abs_start=250.0)
        chosen = adapter._disambiguate_candidates([main_seg, branch_seg], 350.0)
        assert chosen is not None
        assert chosen.seg_id == 5  # 进路指向侧线
