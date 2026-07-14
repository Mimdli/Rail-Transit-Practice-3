"""Web ATS 接口的最小集成验证。"""

from fastapi.testclient import TestClient

from src.web.app import app
from src.web.simulation import SimulationRuntime
from src.logger.evaluator import Evaluator
from src.common.track_position import TrackPosition


def test_smoothness_uses_jerk_trend_and_actual_frame_time():
    """恒定加速度应比频繁改变加速度更平稳，且使用帧内真实时间。"""
    def frames(speeds):
        return [
            {"simTime": float(index), "trains": [{
                "id": "1车", "speedKmh": speed * 3.6,
                "position": float(index * 10), "status": "运行",
            }]}
            for index, speed in enumerate(speeds)
        ]

    steady_score, steady_basis = Evaluator.smoothness_metrics(
        frames([0, 1, 2, 3, 4, 5, 6]))
    rough_score, rough_basis = Evaluator.smoothness_metrics(
        frames([0, 2, 0, 2, 0, 2, 0]))

    assert steady_basis["jerkP95"] == 0.0
    assert rough_basis["jerkP95"] > 0.0
    assert steady_score > rough_score


def test_web_switch_route_locks_and_guides_train_to_reverse_branch():
    """SW-0100 应让上行车经 Seg 44/17 进入下行站台。"""
    runtime = SimulationRuntime()
    down_train, train = list(runtime.dispatch.trains.values())
    train_id = train.train_id

    rejected = runtime.request_switch_route(
        down_train.train_id, [4], "SW-0100 上行转下行")
    assert rejected["ok"] is False
    assert "上行股道列车" in rejected["message"]

    # 构造 2车返抵郭公庄前的状态；下行站台必须先由 1车腾空。
    down_train.controller.reset_states(100, 10.0)
    runtime.dispatch.interlocking.cancel_route(train_id)
    train.reserved_segments = ()
    train.track_adapter.set_active_route(None)
    train.target_station_id = None
    train.plan_index = 1
    train.plan_step = -1
    train.controller.reset_states(43, 1.0)
    runtime.dispatch.occupancy.update(runtime.dispatch.trains.values())

    result = runtime.request_switch_route(
        train_id, [4], "SW-0100 上行转下行")

    assert result["ok"] is True
    assert train.reserved_segments == (43, 44, 17, 14, 13)
    assert {17, 44}.issubset(train.reserved_segments)
    assert runtime.dispatch.interlocking.locked_by(17) == train_id
    assert runtime.dispatch.operator_routes[train_id]["componentIds"] == (4,)

    entered = train.track_adapter.advance_position(
        TrackPosition(43, 1.0), -2.0)
    assert entered.segment_id == 44

    cancelled = runtime.cancel_switch_route(train_id)
    assert cancelled["ok"] is True
    assert runtime.dispatch.operator_routes == {}
    assert runtime.dispatch.interlocking.locked_by(17) is None

    assert runtime.request_switch_route(
        train_id, [4], "SW-0100 上行转下行")["ok"]
    for state in train.controller.states:
        state.position = TrackPosition(13, 100.0)
    runtime.dispatch._release_cleared_operator_routes(
        runtime.dispatch.trains.values())
    assert runtime.dispatch.operator_routes == {}
    assert runtime.dispatch.interlocking.locked_by(17) is None


def test_web_switch_routes_cover_sw0101_and_sw0103():
    """SW-0101 中部渡线和 SW-0103 右端渡线均应生成唯一真实进路。"""
    runtime = SimulationRuntime()
    first, second = list(runtime.dispatch.trains.values())

    # SW-0101：上行股道向右进入下行股道，继续运行至 QLZ。
    runtime.dispatch.interlocking.cancel_route(first.train_id)
    first.reserved_segments = ()
    first.track_adapter.set_active_route(None)
    first.target_station_id = None
    first.plan_index = 4
    first.plan_step = 1
    first.controller.reset_states(78, 1.0)
    runtime.dispatch.occupancy.update(runtime.dispatch.trains.values())
    result = runtime.request_switch_route(
        first.train_id, [8], "SW-0101 上行转下行")
    assert result["ok"] is True
    assert first.reserved_segments[:4] == (78, 80, 65, 64)
    assert runtime.cancel_switch_route(first.train_id)["ok"] is True

    # SW-0103：右端下行股道向左进入上行站台，终点为 GTG。
    first.controller.reset_states(100, 10.0)
    runtime.dispatch.interlocking.cancel_route(second.train_id)
    second.reserved_segments = ()
    second.track_adapter.set_active_route(None)
    second.target_station_id = None
    second.plan_index = 11
    second.plan_step = 1
    second.controller.reset_states(211, 6.0)
    runtime.dispatch.occupancy.update(runtime.dispatch.trains.values())
    result = runtime.request_switch_route(
        second.train_id, [14], "SW-0103 下行转上行")
    assert result["ok"] is True
    assert second.reserved_segments == (211, 212, 224, 222, 221, 220)


def test_web_network_sources_include_live_vision_data():
    """Web 联调应向视景模块提供列车、信号和道岔实时状态。"""
    runtime = SimulationRuntime()
    runtime._setup_network_sources()

    source = runtime.network.vision._data_source
    assert source is not None

    data = source()
    primary = next(iter(runtime.dispatch.trains.values()))
    head_state = primary.controller.states[0]
    vision_position = runtime.vision_mapper.to_vision_position(
        head_state.position, primary.controller.direction)
    assert vision_position is not None
    assert data["edge_id"] == vision_position.edge_id
    assert data["position_mm"] == int(round(vision_position.offset_m * 1000))
    # 初始下行车位于郭公庄站，新标注应编码为视景边 11、边内 71.81m。
    assert data["edge_id"] == 11
    assert data["position_mm"] == 71810

    # 按真实变长数组计算偏移，确认最终 UDP 字节中的位置和边号没有被改写。
    import struct
    from src.network.codec import pack_vision_tcms2view
    packet = pack_vision_tcms2view(live_counter=1, **data)
    position_offset = (
        4 + 1 + len(data["signal_states"])
        + 1 + len(data["switch_states"])
        + 4 + 2 + 1 + 1
    )
    assert struct.unpack_from("<i", packet, position_offset)[0] == 71810
    assert struct.unpack_from("<h", packet, position_offset + 4)[0] == 11
    assert len(data["signal_states"]) == len(runtime.track.signals)
    assert len(data["switch_states"]) == len(runtime.ats_switches)
    assert len(data["other_trains"]) == len(runtime.dispatch.trains) - 1

    # 办理反位进路后，对应道岔状态应同步为 0x02。
    reverse_segment = runtime.ats_switches[0].reverse_seg_id
    primary.reserved_segments = (reverse_segment,)
    assert source()["switch_states"][0] == 0x02


def test_network_stats_expose_all_physical_endpoints():
    """Web 状态应拆分 PLC 三端口和司机台两块屏。"""
    runtime = SimulationRuntime()
    stats = runtime.network.network_stats
    endpoints = {
        endpoint["id"]: endpoint
        for module in stats.values()
        for endpoint in module["endpoints"]
    }

    assert set(endpoints) == {
        "vehicle_udp", "signal_gateway", "vision",
        "plc_8001", "plc_8002", "plc_8003",
        "cab_network", "cab_signal",
    }
    assert endpoints["plc_8001"]["remote"] == "192.168.100.123:8001"
    assert endpoints["cab_network"]["remote"] == "192.168.100.121:8888"
    assert endpoints["cab_network"]["frame_size"] == "570 / 570 B"
    assert endpoints["cab_signal"]["remote"] == "192.168.100.122:9999"
    assert endpoints["cab_signal"]["frame_size"] == "68 B"
    assert endpoints["vision"]["local"] == "192.168.100.67:8303"
    assert endpoints["vision"]["remote"] == "192.168.100.124:8303"


def test_cab_and_plc_outputs_share_live_simulation_state():
    """页面、两块屏和PLC周期输出应使用同一份实时状态。"""
    from src.network.codec import pack_network_screen, pack_signal_screen
    from src.vehicle.enums import RunningMode

    runtime = SimulationRuntime()
    runtime._setup_network_sources()
    primary = next(iter(runtime.dispatch.trains.values()))
    primary.controller.set_running_mode(RunningMode.AUTOMATIC)
    for state in primary.controller.states:
        state.velocity = 10.0

    runtime._update_cab_display_from_simulation()
    runtime._sync_plc_output_from_simulation()
    display = runtime.cab_display_state
    network_data = runtime.network.cab_display._network_data_source()
    signal_data = runtime.network.cab_display._signal_data_source()

    assert display["speed"] == 10.0
    assert display["speed_limit"] != 80.0
    assert display["end_station"] != display["curr_station"]
    assert len(display["door_states"]) == 6
    assert display["sig_state"] in (1, 2, 4)
    assert network_data == display
    assert signal_data["speed"] == 36.0
    assert signal_data["mode"] == 4
    network_keys = {
        "speed", "acceleration", "speed_limit", "run_mode", "run_dir",
        "power_pull", "net_pressure", "curr_station", "next_station",
        "end_station", "power_state", "door_states", "has_power", "train_no",
    }
    signal_keys = {
        "speed", "acceleration", "speed_limit", "mode", "run_dir",
        "curr_station", "next_station", "end_station", "pull_switch",
        "pull_state", "brake_state", "urgency_stop", "event_id",
        "sig_state", "train_no", "next_station_dist",
    }
    assert len(pack_network_screen(**{
        key: network_data[key] for key in network_keys})) == 570
    signal_packet = pack_signal_screen(**{
        key: signal_data[key] for key in signal_keys})
    assert len(signal_packet) == 68
    assert signal_packet[56] == 4
    assert runtime.plc_output_vehicle_speed == 36
    assert runtime.plc_output_state["indicator_hv_contactor"] is True
    assert runtime.plc_output_state["indicator_door_closed"] is True


def test_plc_manual_override_keeps_bits_but_not_stale_speed():
    """手动覆盖应保持16位开关量，但车辆速度仍自动更新。"""
    runtime = SimulationRuntime()
    primary = next(iter(runtime.dispatch.trains.values()))

    result = runtime.set_plc_raw_output(0x8001)
    assert result["ok"] is True
    assert result["delivery"] == "saved"
    assert "已保存并保持" in result["message"]
    assert runtime.plc_output_manual_override is True
    assert runtime.plc_output_state["indicator_hv_contactor"] is True
    assert runtime.plc_output_state["btn_close_right"] is True

    for state in primary.controller.states:
        state.velocity = 5.0
    runtime._sync_plc_output_from_simulation()
    assert runtime.plc_output_vehicle_speed == 18
    assert runtime.plc_output_state["btn_close_right"] is True

    runtime.set_plc_output({"manual_override": False})
    assert runtime.plc_output_manual_override is False
    assert runtime.plc_output_state["btn_close_right"] is True


def test_plc_output_api_has_single_handler_per_method():
    """避免重复路由让ATP请求被较早注册的处理器截获。"""
    for method in ("GET", "POST"):
        matches = [
            route for route in app.routes
            if getattr(route, "path", None) == "/api/plc/output"
            and method in getattr(route, "methods", set())
        ]
        assert len(matches) == 1


def test_snapshot_and_dispatch_command():
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["ok"] is True

        frontend = client.get("/")
        assert frontend.status_code == 200
        assert frontend.headers["cache-control"] == "no-store, max-age=0"
        assert "/styles/app.css" in frontend.text
        assert "/scripts/app.js" in frontend.text
        for asset in ("/styles/tokens.css", "/styles/app.css",
                      "/styles/responsive.css", "/scripts/api.js",
                      "/scripts/dispatch-center.js",
                      "/scripts/train-center.js",
                      "/scripts/signal-center.js",
                      "/scripts/interface-center.js",
                      "/scripts/scene-center.js",
                      "/scripts/replay-center.js",
                      "/scripts/power-center.js",
                      "/scripts/cab-center.js",
                      "/scripts/app.js"):
            response = client.get(asset)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store, max-age=0"

        snapshot = client.get("/api/snapshot")
        assert snapshot.status_code == 200
        data = snapshot.json()
        assert data["type"] == "snapshot"
        assert data["sequence"] >= 1
        assert data["generatedAt"].endswith("+00:00")
        assert data["dataSources"]["simulation"] == "realtime"
        assert data["dataSources"]["replay"] == "recorded"
        assert len(data["networkInterfaces"]) == 5
        assert set(data["alarms"]) == {"active", "critical", "warning"}
        assert len(data["stations"]) == 13
        assert data["trains"][0]["id"] == "1车"
        assert len(data["line"]["stations"]) == 13
        assert data["line"]["directions"]["up"]
        assert data["line"]["directions"]["down"]
        assert data["trains"][0]["linkPosition"] is not None
        assert any(signal["linkPosition"] is not None
                   for signal in data["signals"])
        assert len(data["signals"]) == 157
        # 同名信号机按唯一布局位置独立计算，不能把一处 Z2 的红灯复制到全线。
        z2_signals = [signal for signal in data["signals"]
                      if signal["id"] == "Z2"]
        assert len(z2_signals) > 1
        assert {signal["aspect"] for signal in z2_signals} == {"GREEN", "RED"}
        assert all(signal["aspect"] != "UNKNOWN" for signal in data["signals"])
        assert sum(signal["mapped"] for signal in data["signals"]) == 157
        assert sum(signal["stateSource"] == "runtime"
                   for signal in data["signals"]) == 97
        assert sum(signal["stateSource"] == "derived"
                   for signal in data["signals"]) == 60
        assert data["trackSummary"]["switchCount"] == 60
        assert any(component["role"] == "crossover"
                   for component in data["line"]["switchComponents"])
        assert any(component["role"] == "single-crossover"
                   for component in data["line"]["switchComponents"])
        sw0100_entry = next(component for component in
                            data["line"]["switchComponents"]
                            if component["id"] == 4)
        assert sw0100_entry["movement"] == {
            "fromDirection": "up",
            "toDirection": "down",
            "directionCode": -1,
        }
        assert sw0100_entry["segmentLengths"]["17"] > 0
        sw0101 = next(component for component in
                      data["line"]["switchComponents"]
                      if component["id"] == 8)
        sw0103_left = next(component for component in
                           data["line"]["switchComponents"]
                           if component["id"] == 14)
        assert sw0101["movement"] == {
            "fromDirection": "up", "toDirection": "down",
            "directionCode": 1,
        }
        assert sw0103_left["movement"] == {
            "fromDirection": "down", "toDirection": "up",
            "directionCode": -1,
        }
        assert data["trains"][0]["trackDirection"] == "down"
        assert "acceleration" in data["trains"][0]
        assert "tractiveForceKn" in data["trains"][0]
        assert "brakeForceKn" in data["trains"][0]
        assert "energyKwh" in data["trains"][0]
        assert "carForces" in data["trains"][0]
        assert "energy" in data["trains"][0]
        assert "gradientPermille" in data["trains"][0]
        assert "maxCouplerForceKn" in data["trains"][0]

        mode = client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "mode", "value": "manual"},
        )
        assert mode.status_code == 200
        assert mode.json()["ok"] is True

        door = client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "door", "value": "left"},
        )
        assert door.status_code == 200
        assert door.json()["ok"] is True
        client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "door", "value": "close"},
        )

        level = client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "level", "value": "P1"},
        )
        assert level.status_code == 200
        assert level.json()["ok"] is True
        load = client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "load", "value": "AW3"},
        )
        assert load.status_code == 200
        dwell = client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "dwell", "value": "30"},
        )
        assert dwell.status_code == 200
        speed = client.post("/api/simulation/10")
        assert speed.status_code == 200
        client.post(
            "/api/trains/1%E8%BD%A6/control/action",
            json={"action": "mode", "value": "automatic"},
        )

        command = client.post("/api/trains/1%E8%BD%A6/hold")
        assert command.status_code == 200
        assert command.json()["ok"] is True

        command_snapshot = client.get("/api/snapshot").json()
        web_events = [
            event for event in command_snapshot["events"]
            if event["source"] == "web-ats"
        ]
        assert web_events[-1]["train_id"] == "1车"
        assert web_events[-1]["entity_id"] == "hold"

        scenario = client.post("/api/scenarios/power_outage")
        assert scenario.status_code == 200
        assert scenario.json()["ok"] is True
        scenario_snapshot = client.get("/api/snapshot").json()
        assert scenario_snapshot["activeScenario"] == "power_outage"
        assert scenario_snapshot["power"]["status"] == "POWER_OFF"

        unknown_scenario = client.post("/api/scenarios/not-found")
        assert unknown_scenario.status_code == 409

        adhesion = client.post("/api/scenarios/low_adhesion")
        assert adhesion.status_code == 200
        adhesion_snapshot = client.get("/api/snapshot").json()
        assert adhesion_snapshot["environment"]["weather"] == "rain"
        assert adhesion_snapshot["environment"]["adhesionCoefficient"] == 0.10

        communication = client.post("/api/scenarios/communication_outage")
        assert communication.status_code == 200
        communication_snapshot = client.get("/api/snapshot").json()
        assert communication_snapshot["networkFaultInjected"] is True
        assert not any(communication_snapshot["network"].values())

        conflict = client.post("/api/scenarios/occupancy_conflict")
        assert conflict.status_code == 200
        conflict_snapshot = client.get("/api/snapshot").json()
        assert any("场景占用" in owners
                   for owners in conflict_snapshot["occupancy"].values())

        normal = client.post("/api/scenarios/normal")
        assert normal.status_code == 200
        normal_snapshot = client.get("/api/snapshot").json()
        assert normal_snapshot["environment"]["weather"] == "dry"
        assert normal_snapshot["networkFaultInjected"] is False
        assert not any("场景占用" in owners
                       for owners in normal_snapshot["occupancy"].values())

        replay = client.get("/api/replay")
        assert replay.status_code == 200
        assert replay.json()["sampleIntervalMs"] == 500
        assert set(replay.json()["dimensions"]) == {
            "safety", "punctuality", "smoothness", "energy",
            "operation", "stopAccuracy",
        }
        assert replay.json()["grade"] in {"优秀", "良好", "合格", "不合格"}

        with client.websocket_connect("/ws/realtime") as websocket:
            live = websocket.receive_json()
            assert live["type"] == "snapshot"
            assert live["trains"][0]["status"] in {"运行", "进路等待", "扣车"}
