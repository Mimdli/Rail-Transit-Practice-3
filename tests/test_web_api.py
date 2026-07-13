"""Web ATS 接口的最小集成验证。"""

from fastapi.testclient import TestClient

from src.web.app import app
from src.logger.evaluator import Evaluator


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
