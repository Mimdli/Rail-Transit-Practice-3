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
    """Web 办理进路后应锁闭真实区段，并让车辆适配器选择反位支线。"""
    runtime = SimulationRuntime()
    train_id = "1车"

    result = runtime.request_switch_route(train_id, [4, 5], "SW-0100")

    assert result["ok"] is True
    train = runtime.dispatch.trains.require(train_id)
    assert {17, 44}.issubset(train.reserved_segments)
    assert runtime.dispatch.interlocking.locked_by(17) == train_id
    assert runtime.dispatch.operator_routes[train_id]["componentIds"] == (4, 5)

    switch_segment = runtime.track._seg_map[14]
    entered = train.track_adapter.advance_position(
        TrackPosition(14, switch_segment.length - 1.0), 2.0)
    assert entered.segment_id == 17

    cancelled = runtime.cancel_switch_route(train_id)
    assert cancelled["ok"] is True
    assert runtime.dispatch.operator_routes == {}
    assert runtime.dispatch.interlocking.locked_by(17) is None

    assert runtime.request_switch_route(train_id, [4, 5], "SW-0100")["ok"]
    train.controller.reset_states(49, 90.0)
    runtime.dispatch.step(0.1)
    assert runtime.dispatch.operator_routes == {}
    assert runtime.dispatch.interlocking.locked_by(17) is None


def test_web_network_sources_include_live_vision_data():
    """Web 联调应向视景模块提供列车、信号和道岔实时状态。"""
    runtime = SimulationRuntime()
    runtime._setup_network_sources()

    source = runtime.network.vision._data_source
    assert source is not None

    data = source()
    primary = next(iter(runtime.dispatch.trains.values()))
    head_state = primary.controller.states[0]
    assert data["edge_id"] == head_state.position.segment_id
    assert data["position_mm"] == int(head_state.position.offset * 1000)
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
    assert endpoints["vision"]["local"] == "192.168.100.124:8303"


def test_cab_and_plc_outputs_share_live_simulation_state():
    """页面、两块屏和PLC周期输出应使用同一份实时状态。"""
    from src.network.codec import pack_network_screen, pack_signal_screen

    runtime = SimulationRuntime()
    runtime._setup_network_sources()
    primary = next(iter(runtime.dispatch.trains.values()))
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
    assert len(pack_signal_screen(**{
        key: signal_data[key] for key in signal_keys})) == 68
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
        assert data["trackSummary"]["switchCount"] == 60
        assert any(component["role"] == "crossover"
                   for component in data["line"]["switchComponents"])
        assert any(component["role"] == "single-crossover"
                   for component in data["line"]["switchComponents"])
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
