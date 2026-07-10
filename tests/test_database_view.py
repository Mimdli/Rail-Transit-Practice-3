"""数据库线路加载与运营语义模型测试。"""

from src.track.db_loader import DBLoader
from src.track.loader import TrackLoader
from src.track.semantic_line import build_semantic_line
from src.dispatch import DispatchManager, TrainStatus


def test_database_missing_station_positions_are_repaired_in_memory():
    track = DBLoader().load_from_db()
    positions = [station.position for station in track.stations]

    assert len(track.stations) == 13
    assert all(position > 0 for position in positions)
    assert len(set(round(position, 2) for position in positions)) == 13
    assert any("KYL" in warning for warning in track.data_warnings)


def test_demo_semantic_links_exclude_lateral_segments():
    track = TrackLoader().load_demo_data()
    model = build_semantic_line(track)

    assert [link.seg_ids for link in model.links] == [(1,), (2,), (3,)]
    branch_ids = {segment_id for branch in model.branches
                  for segment_id in branch.seg_ids}
    assert branch_ids == {5, 6, 7, 8}


def test_database_semantic_model_uses_all_thirteen_stations():
    track = DBLoader().load_from_db()
    model = build_semantic_line(track)

    assert len(model.stations) == 13
    assert len(model.links) == 12
    assert all(link.seg_ids for link in model.links)


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

    assert leader.controller.states[0].position.segment_id == 69
    segment = track._seg_map[68]
    follower.controller.reset_states(68, 2300.0 - segment.abs_start)
    follower.status = TrainStatus.MANUAL
    for state in follower.controller.states:
        state.velocity = 17.0

    dispatch.step(0.033)

    assert follower.status == TrainStatus.BLOCKED
    assert "前车" in follower.blocked_reason
    assert follower.controller.brake_level >= 0.85
