"""最新视景公里标、边号和站台范围的集成验证。"""

import math

from src.dispatch import DispatchManager, ServicePlan
from src.dispatch.train_manager import resolve_station_track_position
from src.door.interlock import DoorInterlock
from src.track.db_loader import DBLoader
from src.track.vision_alignment import (
    VisionCoordinateMapper,
    load_vision_alignment,
)
from src.vehicle.enums import DoorSide


def test_latest_vision_annotation_is_complete_and_continuous():
    edges, platforms = load_vision_alignment()

    assert len(edges["up"]) == 13
    assert len(edges["down"]) == 12
    assert len(platforms) == 26
    assert {item.station_id for item in platforms} == set(range(1, 14))
    assert all(
        math.isclose(left.end_m, right.start_m, abs_tol=1e-9)
        for direction_edges in edges.values()
        for left, right in zip(direction_edges, direction_edges[1:])
    )
    assert all(
        math.isclose(item.stop_end_m - item.stop_start_m, 118.0,
                     abs_tol=1e-6)
        for item in platforms
    )


def test_all_station_targets_map_to_directional_head_stops_and_door_sides():
    track = DBLoader().load_from_db()
    mapper = VisionCoordinateMapper(track)
    _, platforms = load_vision_alignment()

    assert mapper.available
    for annotation in platforms:
        direction = 1 if annotation.direction == "down" else -1
        station = next(
            item for item in track.stations
            if item.station_id == annotation.station_id)
        target = resolve_station_track_position(track, station, direction)
        line_m = mapper.to_vision_line_m(target, direction)
        matched = mapper.platform_at(target, direction)

        expected = (annotation.stop_end_m
                    if annotation.direction == "down"
                    else annotation.stop_start_m)
        assert math.isclose(line_m, expected, abs_tol=1e-6)
        assert matched is not None
        assert matched.station_id == annotation.station_id
        assert matched.platform_side == annotation.platform_side


def test_mapper_returns_visual_edge_and_edge_relative_offset():
    track = DBLoader().load_from_db()
    mapper = VisionCoordinateMapper(track)
    station = next(item for item in track.stations if item.name == "GGZ")
    target = resolve_station_track_position(track, station, direction=1)

    position = mapper.to_vision_position(target, direction=1)

    assert position is not None
    assert position.edge_id == 11
    assert math.isclose(position.line_m, 431.0, abs_tol=1e-6)
    assert math.isclose(position.offset_m, 130.81, abs_tol=1e-6)


def test_door_interlock_uses_station_specific_platform_side():
    """丰台科技园下行应按新标注开左门，而不是按方向固定开右门。"""
    track = DBLoader().load_from_db()
    dispatch = DispatchManager(track)
    assert dispatch.add_train("D01", start_station_id=2, direction=1).ok
    runtime = dispatch.trains.require("D01")
    station = dispatch.trains.get_station(2)
    target = resolve_station_track_position(track, station, direction=1)
    runtime.controller.reset_to(target)
    interlock = DoorInterlock(
        runtime.controller, track, runtime.track_adapter,
        vision_mapper=dispatch.vision_mapper,
    )

    assert interlock.is_at_platform()
    assert interlock.get_allowed_door_side() == DoorSide.LEFT


def test_dispatch_arrival_aligns_head_to_platform_far_end():
    """下行到站后的车头必须落在站台高里程端停车标。"""
    track = DBLoader().load_from_db()
    dispatch = DispatchManager(track)
    dispatch.add_service_plan(ServicePlan(
        "precision", "精准停车", (1, 2), turnback=False, dwell_time=20.0))
    assert dispatch.add_train("D01", start_station_id=1, direction=1).ok
    assert dispatch.assign_plan("D01", "precision").ok
    assert dispatch.depart("D01").ok
    runtime = dispatch.trains.require("D01")
    target = runtime.auto_drive.target_position
    short = runtime.track_adapter.advance_position(target, -0.1)
    dispatch._place_train_head(runtime, short)

    dispatch._check_arrival(runtime)

    actual = dispatch.vision_mapper.to_vision_line_m(
        runtime.controller.states[0].position, runtime.controller.direction)
    annotation = next(
        item for item in dispatch.vision_mapper.platforms
        if item.station_id == 2 and item.direction == "down")
    assert math.isclose(actual, annotation.stop_end_m, abs_tol=1e-6)
