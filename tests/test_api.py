from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from thermal_sentinel import create_app


def test_health_and_equipment_endpoints(tmp_path: Path) -> None:
    with TestClient(create_app(db_path=tmp_path / "eq.db", policy="adaptive")) as client:
        health = client.get("/health")
        assert health.status_code == 200
        h_body = health.json()
        assert h_body["status"] == "ok"
        assert h_body["database"] == "ok"
        assert h_body["policy"] == "adaptive"
        assert h_body["equipment_count"] == 6

        eq_list = client.get("/api/v1/equipment")
        assert eq_list.status_code == 200
        assert len(eq_list.json()) == 6

        assert client.get("/api/v1/equipment/B1").status_code == 200
        assert client.get("/api/v1/equipment/B99").status_code == 404

        new_bay = client.post(
            "/api/v1/equipment",
            json={"bay_id": "B7", "label": "Bay 07", "x_m": 10.0, "y_m": 3.0, "baseline_temp_c": 29.0},
        )
        assert new_bay.status_code == 201
        dup_bay = client.post(
            "/api/v1/equipment",
            json={"bay_id": "B7", "label": "Bay 07", "x_m": 10.0, "y_m": 3.0, "baseline_temp_c": 29.0},
        )
        assert dup_bay.status_code == 409

        patched = client.patch("/api/v1/equipment/B7/baseline", json={"baseline_temp_c": 28.0})
        assert patched.status_code == 200
        assert patched.json()["baseline_temp_c"] == 28.0


def test_readings_alerts_and_inspection_endpoints(tmp_path: Path) -> None:
    with TestClient(create_app(db_path=tmp_path / "flow.db", policy="adaptive")) as client:
        assert client.post("/api/v1/readings", json={"bay_id": "B1", "temperature_c": 999.0}).status_code == 422

        ts = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC).isoformat()
        single = client.post(
            "/api/v1/readings",
            json={"bay_id": "B1", "temperature_c": 34.0, "sensor_id": "tc-1", "recorded_at": ts},
        )
        assert single.status_code == 201
        assert single.json()["alert_event"] == "opened"

        batch = client.post(
            "/api/v1/readings/batch",
            json={
                "readings": [
                    {"bay_id": "B2", "temperature_c": 28.1, "sensor_id": "tc-2"},
                    {"bay_id": "B3", "temperature_c": 28.0, "sensor_id": "tc-3"},
                ]
            },
        )
        assert batch.status_code == 201
        assert batch.json()["ingested_count"] == 2

        readings = client.get("/api/v1/readings", params={"bay_id": "B1", "limit": 10})
        assert readings.status_code == 200
        assert len(readings.json()) == 1

        alerts = client.get("/api/v1/alerts", params={"status": "open", "bay_id": "B1"})
        assert alerts.status_code == 200
        alert_id = alerts.json()[0]["id"]

        ack = client.post(f"/api/v1/alerts/{alert_id}/acknowledge")
        assert ack.status_code == 200
        assert ack.json()["status"] == "acknowledged"
        assert client.post("/api/v1/alerts/99999/acknowledge").status_code == 404

        next_target = client.get(
            "/api/v1/inspection/next",
            params={"robot_x_m": 0.0, "robot_y_m": 3.0, "log_dispatch": "true"},
        )
        assert next_target.status_code == 200
        assert next_target.json()["bay_id"] == "B1"
        assert next_target.json()["mode"] == "alert"

        verify = client.post(
            "/api/v1/inspection/verify",
            json={"bay_id": "B1", "camera_temp_c": 32.0, "robot_x_m": 2.0, "robot_y_m": 1.0},
        )
        assert verify.status_code == 201
        assert verify.json()["log"]["outcome"] == "confirmed"
        assert verify.json()["equipment"]["status"] == "confirmed"

        logs = client.get("/api/v1/inspection/logs", params={"bay_id": "B1"})
        assert logs.status_code == 200
        assert len(logs.json()) == 2


def test_simulation_replay_endpoint(tmp_path: Path) -> None:
    with TestClient(create_app(db_path=tmp_path / "replay.db", policy="bayesian")) as client:
        replay = client.post(
            "/api/v1/simulation/replay",
            json={"seed": 3, "fault_bay_id": "B5", "max_ticks": 140, "reset_first": True},
        )
        assert replay.status_code == 200
        assert replay.json()["recommended_bay"] == "B5"

        bad_replay = client.post(
            "/api/v1/simulation/replay",
            json={"seed": 3, "fault_bay_id": "B9", "max_ticks": 20},
        )
        assert bad_replay.status_code == 400
