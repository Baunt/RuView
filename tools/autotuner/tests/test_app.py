import io
import json
import time
import zipfile

from fastapi.testclient import TestClient

from autotuner.app import create_app
from autotuner.ruview import normalize
from autotuner.store import Store


def test_full_record_fit_alignment_export_flow_without_secret_leak(tmp_path, fit_bytes, replies, monkeypatch):
    monkeypatch.delenv("RUVIEW_TOKEN_FILE", raising=False)
    monkeypatch.delenv("RUVIEW_API_TOKEN", raising=False)
    app = create_app(tmp_path, polling=False)
    with TestClient(app) as client:
        assert client.post("/api/connection", json={"url": "http://127.0.0.1:3000", "source_address": None, "token": "sensitive-test-token"}).status_code == 200
        assert "sensitive-test-token" not in (tmp_path/"connection.json").read_text()
        started = client.post("/api/sessions/start", json={"name": "Protocol test", "initial_block": "seated"}).json()
        sid = started["id"]
        assert client.post("/api/sessions/start", json={"name": "duplicate"}).status_code == 409
        assert client.post("/api/connection", json={"url": "http://localhost:3000"}).status_code == 409
        stamp = time.time()
        app.state.station.store.append(normalize(replies, 1, stamp-.1, stamp, 123))
        assert client.post(f"/api/sessions/{sid}/markers", json={"kind": "sync", "note": "start event"}).status_code == 200
        assert client.post("/api/sessions/stop").status_code == 200
        # Force test bounds to include two complete grid instants without a sleep.
        s = app.state.station.store.get(sid)
        s.update(started_at=1631065600, ended_at=1631065602)
        app.state.station.store.save(s)
        fit = fit_bytes([(1000000000, 70), (1000000001, 71), (1000000002, 72)])
        imported = client.post(f"/api/sessions/{sid}/fit", files={"file": ("fixture.fit", fit)})
        assert imported.status_code == 200, imported.text
        assert imported.json()["quality"]["valid_hr"] == 3
        assert client.post(f"/api/sessions/{sid}/alignment", json={"offset_s": 0, "uncertainty_s": .25}).status_code == 200
        report = client.get(f"/api/sessions/{sid}/report").json()
        assert report["counts"]["eligible_seconds"] == 3
        assert report["coverage"] == 0
        assert report["mae_bpm"] is None
        exported = client.get(f"/api/sessions/{sid}/export")
        archive = zipfile.ZipFile(io.BytesIO(exported.content))
        assert any(name.endswith('.fit') for name in archive.namelist())
        assert 'timeline.csv' in archive.namelist()
        assert all(b'sensitive-test-token' not in archive.read(name) for name in archive.namelist())
        assert client.post(f"/api/sessions/{sid}/fit", files={"file": ("bad.fit", b'bad')}).status_code == 422
        assert client.get('/api/sessions/bad-id/report').status_code == 404
        assert client.post('/api/connection', json={"url": "http://secret@localhost:3000"}).status_code == 422
        assert client.post('/api/sessions/start', json={"name": "x"}, headers={"Origin": "https://unrelated.example"}).status_code == 403
        assert client.get('/api/status', headers={"Host": "unrelated.example"}).status_code == 400


def test_restart_preserves_observations_and_marks_unfinished_session(tmp_path):
    first = Store(tmp_path)
    session = first.start("recovery", "seated", {"node_id": 1})
    stamp = session["started_at"] + 1
    first.append({"received_at": stamp, "status": "unavailable", "hr_bpm": None})
    first.stream.close()
    first.db.close()  # Simulate process exit before the session metadata is finalized.
    second = Store(tmp_path)
    recovered = second.get(session["id"])
    assert recovered["status"] == "interrupted"
    assert recovered["ended_at"] == stamp
    assert len(second.observations(session["id"])) == 1
    second.close()


def test_host_clock_step_cannot_create_valid_comparison_values(tmp_path):
    store = Store(tmp_path)
    session = store.start("clock", "seated", {"node_id": 1})
    anchor = session["clock_anchor"]
    store.append({"received_at": anchor["utc"]+10, "received_monotonic_ns": anchor["monotonic_ns"]+1_000_000_000,
                  "hr_bpm": 72, "br_rpm": 14, "status": "valid", "reasons": []})
    observation = store.observations(session["id"])[0]
    assert observation["clock_valid"] is False
    assert observation["hr_bpm"] is None
    assert observation["reasons"] == ["host_clock_discontinuity"]
    store.close()


def test_raw_attachment_verifies_node_time_overlap_and_payload(tmp_path, monkeypatch):
    from autotuner.raw import CaptureWriter
    import struct
    monkeypatch.delenv("RUVIEW_TOKEN_FILE", raising=False)
    app = create_app(tmp_path / "station", polling=False)
    with TestClient(app) as client:
        session = client.post('/api/sessions/start', json={"name":"raw protocol fixture"}).json()
        sid = session['id']
        client.post('/api/sessions/stop')
        session = app.state.station.store.get(sid)
        now = time.time()
        app.state.station.store.update(sid, {"started_at": now-1, "ended_at": now+1})
        path = tmp_path / 'raw.jsonl.gz'
        writer = CaptureWriter(path)
        payload = struct.pack('<IBBHIIbbBB', 0xC5110001, 1, 1, 2, 2437, 1, -62, -95, 0, 0)+b'\x01\x02'*2
        writer.packet(payload, ('127.0.0.1',5005), utc=now-.1)
        writer.packet(payload, ('127.0.0.1',5005), utc=now+.1)
        writer.close()
        response = client.post(f'/api/sessions/{sid}/raw', files={"file": ('raw.jsonl.gz',path.read_bytes())})
        assert response.status_code == 200, response.text
        assert response.json()['raw_frames'] == 2
        assert response.json()['clock_sync'] == 'unverified'
        updated = app.state.station.store.get(sid)
        assert updated['raw_capture']['raw_frames'] == 2
        assert updated['status'] == 'completed'
        app.state.station.store.update(sid, {"started_at": now+100, "ended_at": now+200})
        assert client.post(f'/api/sessions/{sid}/raw', files={"file": ('raw.jsonl.gz',path.read_bytes())}).status_code == 422
