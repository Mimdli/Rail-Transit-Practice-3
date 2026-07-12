"""Web ATS 接口的最小集成验证。"""

from fastapi.testclient import TestClient

from src.web.app import app


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
        assert "acceleration" in data["trains"][0]
        assert "tractiveForceKn" in data["trains"][0]
        assert "brakeForceKn" in data["trains"][0]
        assert "energyKwh" in data["trains"][0]

        command = client.post("/api/trains/1%E8%BD%A6/depart")
        assert command.status_code == 200
        assert command.json()["ok"] is True

        command_snapshot = client.get("/api/snapshot").json()
        web_events = [
            event for event in command_snapshot["events"]
            if event["source"] == "web-ats"
        ]
        assert web_events[-1]["train_id"] == "1车"
        assert web_events[-1]["entity_id"] == "depart"

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

        with client.websocket_connect("/ws/realtime") as websocket:
            live = websocket.receive_json()
            assert live["type"] == "snapshot"
            assert live["trains"][0]["status"] in {"运行", "进路等待"}
