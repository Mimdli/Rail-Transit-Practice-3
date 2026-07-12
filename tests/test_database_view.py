"""数据库线路加载与运营语义模型测试。"""

from src.track.db_loader import DBLoader
from src.track.loader import TrackLoader
from src.track.semantic_line import build_semantic_line
from src.track.link_mainline import load_mainline_links, mainline_segment_ids
from src.track.link_mainline import LinkCoordinateMapper
from src.common.track_position import TrackPosition
from src.track.ats_layout import load_ats_layout
from src.dispatch import DispatchManager, ServicePlan, TrainStatus


def test_database_missing_station_positions_are_repaired_in_memory():
    track = DBLoader().load_from_db()
    positions = [station.position for station in track.stations]

    assert len(track.stations) == 13
    assert all(position > 0 for position in positions)
    assert len(set(round(position, 2) for position in positions)) == 13
    assert any("KYL" in warning for warning in track.data_warnings)


def test_demo_semantic_links_follow_current_mainline():
    track = TrackLoader().load_demo_data()
    model = build_semantic_line(track)

    assert len(model.links) == len(model.stations) - 1
    assert [link.seg_ids for link in model.links] == [
        (segment.seg_id,) for segment in track.segments[:-1]
    ]
    assert not model.branches


def test_database_semantic_model_uses_all_thirteen_stations():
    track = DBLoader().load_from_db()
    model = build_semantic_line(track)

    assert len(model.stations) == 13
    assert len(model.links) == 12
    assert all(link.seg_ids for link in model.links)


def test_mainline_is_built_from_ordered_link_chains():
    links = load_mainline_links()

    assert len(links["up"]) == 83
    assert len(links["down"]) == 73
    assert all(
        left.end_m == right.start_m
        for direction in links.values()
        for left, right in zip(direction, direction[1:])
    )
    assert {link.link_id for link in links["up"]} <= mainline_segment_ids()
    assert {link.link_id for link in links["down"]} <= mainline_segment_ids()


def test_seg_position_maps_to_continuous_link_coordinate():
    track = DBLoader().load_from_db()
    mapper = LinkCoordinateMapper(track)

    assert mapper.to_link_position(TrackPosition(5, 10.0)) == 40.0
    assert mapper.to_link_position(TrackPosition(5, 220.68)) == 250.68
    assert mapper.to_link_position(TrackPosition(6, 0.0)) == 250.68


def test_ats_switch_and_signal_layout_matches_source_tables():
    track = DBLoader().load_from_db()
    mapper = LinkCoordinateMapper(track)
    switches, signals = load_ats_layout()

    assert len(switches) == 60
    assert len(signals) == 157
    assert all(
        segment_id in track._seg_map
        for switch in switches
        for segment_id in (
            switch.normal_seg_id,
            switch.reverse_seg_id,
            switch.merge_seg_id,
        )
    )
    mainline_signals = [
        signal for signal in signals
        if mapper.link_for_segment(signal.seg_id) is not None
    ]
    assert len(mainline_signals) == 78
    assert all(
        0.0 <= signal.offset_m
        <= mapper.link_for_segment(signal.seg_id).length_m
        for signal in mainline_signals
    )


def test_station_markers_use_platform_link_coordinates():
    track = DBLoader().load_from_db()
    mapper = LinkCoordinateMapper(track)
    model = build_semantic_line(track)

    for station in model.stations:
        platform_segments = {
            platform.seg_id for platform in track.platforms
            if (platform.station_id == station.station_id
                or platform.platform_id in station.platform_ids)
        }
        expected = [
            mapper.midpoint_for_segment(segment_id)
            for segment_id in platform_segments
            if mapper.midpoint_for_segment(segment_id) is not None
        ]
        assert expected
        assert station.position == sum(expected) / len(expected)


def test_database_station_placement_and_separation_use_down_platform_topology():
    """复现 KYL 前车场景，不允许按里程误落到并行 Seg。"""
    track = DBLoader().load_from_db()
    dispatch = DispatchManager(track)
    first_station = min(track.stations, key=lambda station: station.position)
    kyl = next(station for station in track.stations if station.name == "KYL")
    assert dispatch.add_train("后车", first_station.station_id).ok
    assert dispatch.add_train("前车", kyl.station_id, direction=1).ok
    follower = dispatch.trains.require("后车")
    leader = dispatch.trains.require("前车")

    assert leader.controller.states[0].position.segment_id == 55
    segment = track._seg_map[54]
    follower.controller.reset_states(54, 2300.0 - segment.abs_start)
    follower.status = TrainStatus.MANUAL
    for state in follower.controller.states:
        state.velocity = 17.0

    dispatch.step(0.033)

    assert follower.status == TrainStatus.BLOCKED
    assert "前车" in follower.blocked_reason
    assert follower.controller.brake_level >= 0.85


def test_database_station_placement_uses_direction_platforms():
    track = DBLoader().load_from_db()
    dispatch = DispatchManager(track)
    kyl = next(station for station in track.stations if station.name == "KYL")

    assert dispatch.add_train("下行车", kyl.station_id, direction=1).ok
    assert dispatch.add_train("上行车", kyl.station_id, direction=-1).ok
    down_seg = dispatch.trains.require(
        "下行车").controller.states[0].position.segment_id
    up_seg = dispatch.trains.require(
        "上行车").controller.states[0].position.segment_id

    assert down_seg == 55
    assert up_seg != down_seg


def test_database_ggz_to_fsp_uses_one_dispatch_route():
    """GGZ→FSP 的目标、锁闭进路和车辆活动进路必须完全一致。"""
    track = DBLoader().load_from_db()
    dispatch = DispatchManager(track)
    dispatch.add_service_plan(ServicePlan("short", "GGZ → FSP", (1, 2)))
    assert dispatch.add_train("D01", 1, direction=1).ok
    assert dispatch.assign_plan("D01", "short").ok
    assert dispatch.depart("D01").ok
    runtime = dispatch.trains.require("D01")

    expected = (13, 14, 15, 16, 19, 20, 22, 23, 235, 24)
    assert runtime.reserved_segments == expected
    assert runtime.auto_drive.target_position.segment_id == 24
    assert tuple(runtime.track_adapter.get_active_route().seg_ids) == expected
