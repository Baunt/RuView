import copy
import socketserver
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from autotuner.reference import normalize_records, read_fit
from autotuner.report import build_report
from autotuner.ruview import normalize, request_json


def test_fit_crc_timestamps_and_missing_values(fit_bytes):
    result = read_fit(fit_bytes([(1000000000, 70), (1000000001, 255), (1000000004, 72)]))
    assert result["points"][0] == {"timestamp": 1631065600.0, "hr_bpm": 70.0}
    assert result["points"][1]["hr_bpm"] is None
    assert result["quality"]["missing_hr"] == 1
    assert result["quality"]["max_interval_s"] == 3
    assert result["quality"]["gaps_over_2s"] == 1
    damaged = bytearray(fit_bytes([(1000000000, 70)]))
    damaged[-3] ^= 1
    with pytest.raises(Exception):
        read_fit(bytes(damaged))


def test_reference_conflicts_are_not_arbitrarily_selected():
    result = normalize_records([{"timestamp": 2, "heart_rate": 80}, {"timestamp": 1, "heart_rate": 70},
                                {"timestamp": 1, "heart_rate": 90}, {"timestamp": 3, "heart_rate": 0}])
    assert result["points"] == [{"timestamp": 1, "hr_bpm": None}, {"timestamp": 2, "hr_bpm": 80}, {"timestamp": 3, "hr_bpm": None}]
    assert result["quality"]["out_of_order"] == 1
    assert result["quality"]["conflicts"] == 1


@pytest.mark.parametrize("fault", ["stale", "simulation", "multi_node", "abstain", "slow", "node_stale", "auth"])
def test_fail_closed_observations(replies, fault):
    data = copy.deepcopy(replies)
    duration = .1
    if fault == "stale": data["health"]["processing"]["last_update_age_ms"] = 5000
    if fault == "simulation": data["vitals"]["source"] = "simulated"
    if fault == "multi_node": data["nodes"]["nodes"].append({"node_id": 2, "csi_status": "active"})
    if fault == "abstain": data["vitals"]["authority"] = "abstained"
    if fault == "slow": duration = 9
    if fault == "node_stale": data["nodes"]["nodes"][0]["csi_last_seen_ms"] = 9000
    if fault == "auth": data["vitals"] = {"error": "HTTP 401"}
    observation = normalize(data, 1, 100, 100+duration, 500)
    assert observation["hr_bpm"] is None
    assert observation["br_rpm"] is None
    assert observation["reasons"]


def test_valid_public_observation_does_not_invent_time(replies):
    result = normalize(replies, 1, 100, 100.1, 500)
    assert result["hr_bpm"] == 72
    assert result["source"] == "ruview_public_vitals"
    assert result["observed_until"] is None
    assert result["window_seconds"] is None


@pytest.mark.parametrize("code", [401, 404, 503])
def test_optional_sensing_diagnostics_do_not_invalidate_authorized_public_vitals(replies, code):
    replies["sensing"] = {"error": f"HTTP {code}", "http_status": code}
    result = normalize(replies, 1, 100, 100.1, 500)
    assert result["hr_bpm"] == 72
    assert result["status"] == "valid"
    assert result["responses"]["sensing"]["http_status"] == code


def test_optional_calibrated_presence_never_hides_required_api_failure(replies):
    replies["sensing"] = {"source": "esp32", "calibrated_presence_evidence": {"presence": True}}
    replies["vitals"] = {"error": "HTTP 401", "http_status": 401}
    result = normalize(replies, 1, 100, 100.1, 500)
    assert result["hr_bpm"] is None
    assert "api_auth_required" in result["reasons"]


def test_malformed_optional_fields_abstain_instead_of_killing_poll_loop(replies):
    replies["health"]["processing"] = None
    replies["nodes"]["nodes"] = [None, "bad"]
    replies["vitals"]["vital_signs"] = []
    assert normalize(replies, 1, 100, 100.1, 500)["status"] == "unavailable"


def test_real_http_adapter_binds_source_and_sends_token_only_as_header():
    class LocalServer(ThreadingHTTPServer):
        def server_bind(self):
            # Avoid platform reverse-DNS lookup; the test concerns HTTP, not mDNS.
            socketserver.TCPServer.server_bind(self)
            self.server_name = "localhost"
            self.server_port = self.socket.getsockname()[1]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            assert self.client_address[0] == "127.0.0.1"
            self.send_response(200 if self.headers.get("Authorization") == "Bearer local-test" else 401)
            self.end_headers()
            self.wfile.write(b'{"source":"esp32"}')
        def log_message(self, *_): pass
    server = LocalServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}"
        assert request_json(url, "/health", "local-test", "127.0.0.1")["source"] == "esp32"
        assert request_json(url, "/health")["http_status"] == 401
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_report_keeps_missing_estimates_in_denominator_and_breaks_gaps():
    session = {"id": "test", "started_at": 100, "ended_at": 105, "initial_block": "seated", "alignment": {"offset_s": 0, "uncertainty_s": .2}}
    observations = [{"received_at": 100, "hr_bpm": 72, "status": "valid"},
                    {"received_at": 102, "hr_bpm": None, "status": "unavailable", "reasons": ["publication_abstained"]}]
    reference = {"points": [{"timestamp": t, "hr_bpm": 70} for t in range(100,106)], "quality": {}}
    report = build_report(session, observations, reference, [])
    assert report["counts"]["eligible_seconds"] == 6
    assert report["counts"]["matched_seconds"] == 2
    assert report["coverage"] == pytest.approx(1/3)
    assert report["mae_bpm"] == 2
    assert report["bias_bpm"] == 2
    assert report["timeline"][-1]["ruview_hr"] is None
    assert report == build_report(session, observations, reference, [])


def test_fixed_alignment_no_interpolation_motion_and_out_of_range():
    session = {"id": "test", "started_at": 100, "ended_at": 105, "initial_block": "seated", "alignment": {"offset_s": 10}}
    ref = {"points": [{"timestamp": 90, "hr_bpm": 70}, {"timestamp": 92, "hr_bpm": 150}, {"timestamp": 95, "hr_bpm": 80}], "quality": {}}
    result = build_report(session, [], ref, [{"timestamp": 104, "kind": "motion"}])
    assert result["timeline"][0]["reference_hr"] == 70
    assert result["timeline"][1]["reference_hr"] is None
    assert result["counts"]["eligible_seconds"] == 1
    assert result["counts"]["out_of_range_seconds"] == 1
    assert result["mae_bpm"] is None
    assert result["status"] == "insufficient_data"
