"""多列车调度、进路冲突和折返测试。"""

from src.dispatch import DispatchManager, ServicePlan, TrainStatus
from src.track.loader import TrackLoader


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
