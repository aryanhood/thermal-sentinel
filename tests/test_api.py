from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from thermal_sentinel import create_app


def test_health_and_equipment_endpoints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(db_path=tmp_path / "eq.db", policy="adaptive")
    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        h_body = health.json()
        assert h_body["status"] == "ok"
        assert h_body["database"] == "ok"
        assert h_body["policy"] == "adaptive"
        assert h_body["equipment_count"] == 6

        eq_list = client.get("/api/v1/equipment")
        assert eq_list.status_code == 200
        assert eq_list.json()[0]["sensor_id"] == "thermocouple-B1"

        assert client.get("/api/v1/equipment/B1").status_code == 200
        assert client.get("/api/v1/equipment/B99").status_code == 404

        assert (
            client.post(
                "/api/v1/equipment",
                json={"bay_id": "B7", "label": "   ", "x_m": 10.0, "y_m": 3.0},
            ).status_code
            == 422
        )

        new_bay = client.post(
            "/api/v1/equipment",
            json={"bay_id": "B7", "label": "Bay 07", "sensor_id": "tc-B7", "x_m": 10.0, "y_m": 3.0},
        )
        assert new_bay.status_code == 201
        assert new_bay.json()["sensor_id"] == "tc-B7"

        dup_bay = client.post(
            "/api/v1/equipment",
            json={"bay_id": "B7", "label": "Bay 07", "x_m": 10.0, "y_m": 3.0},
        )
        assert dup_bay.status_code == 409

        patched = client.patch("/api/v1/equipment/B7/baseline", json={"baseline_temp_c": 28.0})
        assert patched.status_code == 200
        assert patched.json()["baseline_temp_c"] == 28.0

        monkeypatch.setattr(app.state.store, "ping", lambda: False)
        degraded = client.get("/health")
        assert degraded.status_code == 503
        assert degraded.json()["status"] == "degraded"


def test_readings_retry_conflict_future_and_alert_endpoints(tmp_path: Path) -> None:
    t0 = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    with TestClient(create_app(db_path=tmp_path / "flow.db", policy="adaptive")) as client:
        assert client.post("/api/v1/readings", json={"bay_id": "B1", "temperature_c": 999.0}).status_code == 422

        future_ts = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        assert (
            client.post(
                "/api/v1/readings", json={"bay_id": "B1", "temperature_c": 28.5, "recorded_at": future_ts}
            ).status_code
            == 422
        )

        wrong_sensor = client.post(
            "/api/v1/readings",
            json={"bay_id": "B1", "temperature_c": 28.5, "sensor_id": "rogue-probe", "recorded_at": t0.isoformat()},
        )
        assert wrong_sensor.status_code == 409

        single = client.post(
            "/api/v1/readings",
            json={"bay_id": "B1", "temperature_c": 34.0, "sensor_id": "thermocouple-B1", "recorded_at": t0.isoformat()},
        )
        assert single.status_code == 201
        assert single.json()["alert_event"] == "opened"
        assert single.json()["duplicate"] is False

        retry = client.post(
            "/api/v1/readings",
            json={"bay_id": "B1", "temperature_c": 34.0, "sensor_id": "thermocouple-B1", "recorded_at": t0.isoformat()},
        )
        assert retry.status_code == 200
        assert retry.json()["duplicate"] is True

        conflict = client.post(
            "/api/v1/readings",
            json={"bay_id": "B1", "temperature_c": 28.0, "sensor_id": "thermocouple-B1", "recorded_at": t0.isoformat()},
        )
        assert conflict.status_code == 409

        alert_id = client.get("/api/v1/alerts", params={"status": "open", "bay_id": "B1"}).json()[0]["id"]
        assert client.post(f"/api/v1/alerts/{alert_id}/acknowledge").status_code == 200
        assert client.post("/api/v1/alerts/99999/acknowledge").status_code == 404
        assert client.post("/api/v1/alerts/0/acknowledge").status_code == 422

        assert client.get("/api/v1/inspection/next", params={"log_dispatch": "true"}).json()["bay_id"] == "B1"

        # Stale inspection before alert timestamp is rejected with 409
        stale_verify = client.post(
            "/api/v1/inspection/verify",
            json={"bay_id": "B1", "camera_temp_c": 28.0, "inspected_at": (t0 - timedelta(seconds=10)).isoformat()},
        )
        assert stale_verify.status_code == 409

        verify = client.post(
            "/api/v1/inspection/verify",
            json={"bay_id": "B1", "camera_temp_c": 32.0, "robot_x_m": 2.0, "robot_y_m": 1.0},
        )
        assert verify.status_code == 201
        assert verify.json()["log"]["outcome"] == "confirmed"
        assert client.post(f"/api/v1/alerts/{alert_id}/acknowledge").status_code == 409
        assert len(client.get("/api/v1/inspection/logs", params={"bay_id": "B1"}).json()) == 2


def test_batch_validation_rollback_late_readings_and_baseline(tmp_path: Path) -> None:
    t0 = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    with TestClient(create_app(db_path=tmp_path / "batch.db", policy="adaptive")) as client:
        assert client.post("/api/v1/readings/batch", json={"readings": []}).status_code == 422
        oversized = [{"bay_id": "B1", "temperature_c": 28.0}] * 1001
        assert client.post("/api/v1/readings/batch", json={"readings": oversized}).status_code == 422

        # Batch failure on item 2 must roll back item 1 completely
        failed_batch = client.post(
            "/api/v1/readings/batch",
            json={
                "readings": [
                    {"bay_id": "B1", "temperature_c": 35.0, "recorded_at": t0.isoformat()},
                    {"bay_id": "B2", "temperature_c": 28.0, "sensor_id": "wrong-sensor", "recorded_at": t0.isoformat()},
                ]
            },
        )
        assert failed_batch.status_code == 409
        assert client.get("/api/v1/readings").json() == []
        assert client.get("/api/v1/alerts").json() == []

        ok_batch = client.post(
            "/api/v1/readings/batch",
            json={
                "readings": [
                    {"bay_id": "B1", "temperature_c": 34.0, "recorded_at": t0.isoformat()},
                    {"bay_id": "B1", "temperature_c": 28.0, "recorded_at": (t0 - timedelta(seconds=5)).isoformat()},
                    {"bay_id": "B2", "temperature_c": 28.1, "recorded_at": t0.isoformat()},
                ]
            },
        )
        assert ok_batch.status_code == 201
        assert ok_batch.json()["out_of_order_count"] == 1
        assert ok_batch.json()["duplicate_count"] == 0
        assert client.get("/api/v1/equipment/B1").json()["alert_active"] is True

        patched = client.patch("/api/v1/equipment/B1/baseline", json={"baseline_temp_c": 30.0})
        assert patched.status_code == 200
        assert client.get("/health").json()["open_alerts"] == 0


def test_isolated_simulation_replay_endpoint_preserves_operational_data(tmp_path: Path) -> None:
    t0 = datetime(2026, 2, 1, 10, 0, 0, tzinfo=UTC)
    with TestClient(create_app(db_path=tmp_path / "isolated_replay.db", policy="adaptive")) as client:
        client.post(
            "/api/v1/readings",
            json={"bay_id": "B1", "temperature_c": 34.0, "recorded_at": t0.isoformat()},
        )
        assert client.get("/api/v1/inspection/next", params={"log_dispatch": "true"}).status_code == 200

        eq_before = client.get("/api/v1/equipment").json()
        readings_before = client.get("/api/v1/readings", params={"limit": 1000}).json()
        alerts_before = client.get("/api/v1/alerts", params={"limit": 1000}).json()
        logs_before = client.get("/api/v1/inspection/logs", params={"limit": 1000}).json()

        replay = client.post(
            "/api/v1/simulation/replay",
            json={"seed": 3, "fault_bay_id": "B5", "max_ticks": 140, "policy": "bayesian"},
        )
        assert replay.status_code == 200
        assert replay.json()["recommended_bay"] == "B5"
        assert replay.json()["policy"] == "bayesian"

        assert client.get("/api/v1/equipment").json() == eq_before
        assert client.get("/api/v1/readings", params={"limit": 1000}).json() == readings_before
        assert client.get("/api/v1/alerts", params={"limit": 1000}).json() == alerts_before
        assert client.get("/api/v1/inspection/logs", params={"limit": 1000}).json() == logs_before

        bad_replay = client.post("/api/v1/simulation/replay", json={"seed": 3, "fault_bay_id": "B9", "max_ticks": 20})
        assert bad_replay.status_code == 400
