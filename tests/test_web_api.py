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

        snapshot = client.get("/api/snapshot")
        assert snapshot.status_code == 200
        data = snapshot.json()
        assert data["type"] == "snapshot"
        assert len(data["stations"]) == 13
        assert data["trains"][0]["id"] == "1车"
        assert len(data["line"]["stations"]) == 13
        assert data["line"]["directions"]["up"]
        assert data["line"]["directions"]["down"]
        assert data["trains"][0]["linkPosition"] is not None
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

        with client.websocket_connect("/ws/realtime") as websocket:
            live = websocket.receive_json()
            assert live["type"] == "snapshot"
            assert live["trains"][0]["status"] in {"运行", "进路等待"}
