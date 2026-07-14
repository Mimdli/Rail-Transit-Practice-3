"""多列车调度、进路冲突和折返测试。"""

from src.dispatch import DispatchManager, ServicePlan, TrainStatus
from src.track.loader import TrackLoader
from src.track.db_loader import DBLoader
from src.dispatch.train_manager import resolve_station_track_position
from src.track.data import Signal
from src.signal.system import SignalAspect, SignalSystem
from src.logger.recorder import Recorder


def _make_dispatch():
    track = TrackLoader().load_demo_data()
    dispatch = DispatchManager(track)
    dispatch.add_service_plan(ServicePlan(
        "loop", "站A ⇆ 站D", (1, 2, 3, 4), turnback=True, dwell_time=0.1,
    ))
    return dispatch


def test_add_assign_and_depart_train():
    dispatch = _make_dispatch()
    assert dispatch.add_train("2车", 1).ok
    assert dispatch.assign_plan("2车", "loop").ok
    assert dispatch.depart("2车").ok

    runtime = dispatch.trains.require("2车")
    assert runtime.status == TrainStatus.RUNNING
    assert runtime.target_station_id == 2
    assert runtime.reserved_segments


def test_dispatch_writes_structured_train_events(tmp_path):
    track = TrackLoader().load_demo_data()
    recorder = Recorder(log_dir=str(tmp_path))
    recorder.start()
    dispatch = DispatchManager(track, recorder=recorder)
    dispatch.add_service_plan(ServicePlan(
        "loop", "站A ⇆ 站D", (1, 2, 3, 4), turnback=True,
    ))
    assert dispatch.add_train("2车", 1).ok
    assert dispatch.assign_plan("2车", "loop").ok
    assert dispatch.depart("2车").ok

    departures = recorder.query(
        event_type="发车", train_id="2车", source="dispatch")
    assert len(departures) == 1
    assert recorder.get_summary()["发车次数"] == 1
    recorder.close()


def test_manual_train_brakes_for_stationary_train_ahead(tmp_path):
    """人工驾驶也必须受独立列车间隔防护（双链中两车须在同一链上）。"""
    track = TrackLoader().load_demo_data()
    recorder = Recorder(log_dir=str(tmp_path))
    recorder.start()
    dispatch = DispatchManager(track, recorder=recorder)
    # 两车均加入后，手动将后车重置到前车同链（UP链 seg1→seg2）
    assert dispatch.add_train("后车", 1).ok
    assert dispatch.add_train("前车", 2).ok
    follower = dispatch.trains.require("后车")
    leader = dispatch.trains.require("前车")
    # 同一区段内构造约 3m 净间距，避免测试依赖已变化的跨段拓扑。
    follower.controller.direction = 1
    leader.controller.direction = 1
    follower.controller.reset_states(1, 120.0)
    leader.controller.reset_states(1, 240.0)
    follower.status = TrainStatus.MANUAL
    follower.controller.set_throttle(1.0)

    dispatch.step(0.033)

    assert follower.status == TrainStatus.BLOCKED
    assert follower.blocked_reason.startswith("前车")
    assert follower.controller.interlock.traction_permitted is False
    assert follower.controller.brake_level >= 0.85
    events = recorder.query(
        event_type="安全防护", train_id="后车", source="safety")
    assert len(events) == 1
    assert events[0].entity_id == "前车"
    recorder.close()


def test_body_overlap_emergency_stops_both_trains(tmp_path):
    """车体重叠后双方都应紧急制动并记录严重事件（双链中两车须在同一链上）。"""
    track = TrackLoader().load_demo_data()
    recorder = Recorder(log_dir=str(tmp_path))
    recorder.start()
    dispatch = DispatchManager(track, recorder=recorder)
    assert dispatch.add_train("后车", 1).ok
    assert dispatch.add_train("前车", 2).ok
    follower = dispatch.trains.require("后车")
    leader = dispatch.trains.require("前车")
    # 同一区段内让两车车体重叠约 7m，验证碰撞保底逻辑。
    follower.controller.direction = 1
    leader.controller.direction = 1
    follower.controller.reset_states(1, 130.0)
    leader.controller.reset_states(1, 240.0)
    follower.status = TrainStatus.MANUAL

    dispatch.step(0.033)

    assert follower.status == TrainStatus.EMERGENCY_STOP
    assert leader.status == TrainStatus.EMERGENCY_STOP
    assert follower.controller.interlock.emergency_brake_required
    assert leader.controller.interlock.emergency_brake_required
    assert follower.controller.interlock.traction_permitted is False
    assert leader.controller.interlock.traction_permitted is False
    collisions = recorder.query(
        event_type="列车碰撞", severity="CRITICAL")
    assert len(collisions) == 1
    recorder.close()


def test_separation_protection_clears_after_distance_recovers():
    dispatch = _make_dispatch()
    assert dispatch.add_train("后车", 1).ok
    assert dispatch.add_train("前车", 2).ok
    follower = dispatch.trains.require("后车")
    leader = dispatch.trains.require("前车")
    # 同一区段内先触发间隔制动，再把前车移至远处验证状态清理。
    follower.controller.direction = 1
    leader.controller.direction = 1
    follower.controller.reset_states(1, 120.0)
    leader.controller.reset_states(1, 240.0)
    follower.status = TrainStatus.MANUAL
    dispatch.step(0.033)
    assert follower.status == TrainStatus.BLOCKED

    # 前车移动到更远的位置（seg3, offset=200 → abs=700），解除阻塞
    leader.controller.reset_states(3, 200.0)
    dispatch.step(0.033)

    assert follower.status == TrainStatus.MANUAL
    assert follower.blocked_reason == ""
    assert follower.controller.brake_level == 0.0


def test_opposing_train_ahead_uses_relative_closing_speed():
    dispatch = _make_dispatch()
    assert dispatch.add_train("下行车", 1).ok
    assert dispatch.add_train("上行车", 2, direction=-1).ok
    down = dispatch.trains.require("下行车")
    up = dispatch.trains.require("上行车")
    down.controller.reset_states(1, 100.0)
    up.controller.reset_states(1, 200.0)
    down.status = TrainStatus.MANUAL
    up.status = TrainStatus.MANUAL
    for state in down.controller.states:
        state.velocity = 15.0
    for state in up.controller.states:
        state.velocity = -5.0

    dispatch.step(0.033)

    assert down.status == TrainStatus.BLOCKED
    assert "上行车" in down.blocked_reason


def test_add_train_rejects_occupied_start_station():
    dispatch = _make_dispatch()
    assert dispatch.add_train("2车", 1).ok
    result = dispatch.add_train("3车", 1)
    assert not result.ok
    assert "占用" in result.message


def test_manual_train_can_be_driven_through_dispatch_clock():
    dispatch = _make_dispatch()
    assert dispatch.add_train("2车", 1).ok
    runtime = dispatch.trains.require("2车")
    runtime.status = TrainStatus.MANUAL
    start = runtime.head_abs
    runtime.controller.set_throttle(1.0)

    for _ in range(30):
        dispatch.step(0.033)

    assert runtime.head_abs > start
    assert runtime.controller.head_speed > 0


def test_dispatch_signal_red_applies_vehicle_protection():
    track = TrackLoader().load_demo_data()
    # 信号须与列车在同一链上（列车默认入站方向为 DOWN 链 seg5）
    track.signals = [Signal(
        "D01", position=200.0, direction="down", seg_id=5, offset=200.0)]
    signal_system = SignalSystem()
    dispatch = DispatchManager(track, signal_system=signal_system)
    assert dispatch.add_train("2车", 1).ok
    runtime = dispatch.trains.require("2车")
    runtime.status = TrainStatus.MANUAL

    signal_system.set_signal_aspect("D01", SignalAspect.RED)
    assert dispatch._apply_signal_protection(runtime)
    assert runtime.status == TrainStatus.BLOCKED
    assert runtime.controller.interlock.traction_permitted is False
    assert runtime.controller.brake_level > 0


def test_conflicting_rolling_routes_are_rejected():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 1)
    dispatch.add_train("3车", 2)
    dispatch.assign_plan("2车", "loop")
    dispatch.assign_plan("3车", "loop")

    assert dispatch.depart("3车").ok
    result = dispatch.depart("2车")

    assert not result.ok
    assert "锁闭" in result.message or "占用" in result.message


def test_hold_emergency_and_restore_commands():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 1)
    dispatch.assign_plan("2车", "loop")
    dispatch.depart("2车")

    assert dispatch.hold("2车").ok
    assert dispatch.trains.require("2车").status == TrainStatus.HELD
    assert dispatch.emergency_stop("2车").ok
    assert dispatch.trains.require("2车").status == TrainStatus.EMERGENCY_STOP
    assert dispatch.restore("2车").ok
    assert dispatch.trains.require("2车").status == TrainStatus.WAITING


def test_hold_and_emergency_cannot_be_bypassed_by_departure():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 1)
    dispatch.assign_plan("2车", "loop")
    assert dispatch.depart("2车").ok

    assert dispatch.hold("2车").ok
    assert not dispatch.depart("2车").ok
    assert dispatch.release("2车").ok
    runtime = dispatch.trains.require("2车")
    assert runtime.status == TrainStatus.WAITING
    assert runtime.controller.head_speed <= 0.1

    assert dispatch.emergency_stop("2车").ok
    assert not dispatch.depart("2车").ok
    assert dispatch.restore("2车").ok
    assert not dispatch.restore("2车").ok


def test_running_train_cannot_depart_twice():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 1)
    dispatch.assign_plan("2车", "loop")
    assert dispatch.depart("2车").ok
    assert not dispatch.depart("2车").ok


def test_dispatch_reserved_route_is_vehicle_active_route():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 1)
    dispatch.assign_plan("2车", "loop")
    assert dispatch.depart("2车").ok
    runtime = dispatch.trains.require("2车")

    active_route = runtime.track_adapter.get_active_route()
    assert active_route is not None
    assert tuple(active_route.seg_ids) == runtime.reserved_segments
    assert runtime.auto_drive.target_position.segment_id \
        == runtime.reserved_segments[-1]


def test_terminal_plan_assignment_reverses_direction():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 4)
    runtime = dispatch.trains.require("2车")
    assert runtime.controller.direction == 1

    assert dispatch.assign_plan("2车", "loop").ok
    assert runtime.controller.direction == -1
    assert runtime.plan_step == -1
    assert dispatch.depart("2车").ok
    assert runtime.target_station_id == 3


def test_reverse_direction_keeps_train_geometry_and_changes_target_sign():
    dispatch = _make_dispatch()
    dispatch.add_train("2车", 4)
    runtime = dispatch.trains.require("2车")
    before = sorted(runtime.track_adapter.to_absolute(state.position)
                    for state in runtime.controller.states)

    assert runtime.controller.reverse_direction()
    after = sorted(runtime.track_adapter.to_absolute(state.position)
                   for state in runtime.controller.states)

    assert before == after
    assert runtime.controller.direction == -1

    start_head = runtime.head_abs
    runtime.controller.set_throttle(1.0)
    for _ in range(30):
        runtime.controller.step(0.033)
    assert runtime.head_abs < start_head


def test_database_terminal_turnback_aligns_train_with_up_platform():
    """数据库双线终点换端后，车体、占用起点和返程进路必须位于同一股道。"""
    track = DBLoader().load_from_db()
    stations = sorted(track.stations, key=lambda item: item.position)
    dispatch = DispatchManager(track)
    dispatch.add_service_plan(ServicePlan(
        "db-loop", "数据库折返", tuple(item.station_id for item in stations),
        turnback=True, dwell_time=0.1,
    ))
    terminal = stations[-1]
    assert dispatch.add_train("折返车", terminal.station_id, direction=1).ok
    assert dispatch.assign_plan("折返车", "db-loop").ok

    runtime = dispatch.trains.require("折返车")
    expected = resolve_station_track_position(track, terminal, direction=-1)
    assert runtime.controller.direction == -1
    assert runtime.controller.states[0].position.segment_id == expected.segment_id

    assert dispatch.depart("折返车").ok
    assert runtime.controller.states[0].position.segment_id in runtime.reserved_segments
    assert runtime.reserved_segments[-1] == resolve_station_track_position(
        track, stations[-2], direction=-1).segment_id


def test_stopped_train_recovers_from_small_station_overshoot():
    """列车小幅越过停车点并停稳后仍应完成到站，不得永久保持全制动。"""
    dispatch = _make_dispatch()
    assert dispatch.add_train("越站车", 1).ok
    assert dispatch.assign_plan("越站车", "loop").ok
    assert dispatch.depart("越站车").ok
    runtime = dispatch.trains.require("越站车")
    target = runtime.auto_drive.target_position
    overshot = runtime.track_adapter.advance_position(target, 5.0)
    dispatch._place_train_head(runtime, overshot)

    dispatch._check_arrival(runtime)

    assert runtime.status == TrainStatus.DWELLING
    assert runtime.target_station_id is None
    assert runtime.controller.states[0].position == target
