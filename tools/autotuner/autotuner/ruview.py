"""Read-only adapter. Credentials never enter stored observations."""
import http.client
import math
import time
from urllib.parse import urlsplit

REQUIRED_PATHS = {"health": "/health", "nodes": "/api/v1/nodes",
                  "calibration": "/api/v1/calibration/status", "vitals": "/api/v1/vital-signs"}
# Retain the original calibrated presence/room diagnostics separately from the
# node heuristic. Older servers can lack this endpoint; public-vitals gates
# continue to depend on the four required responses only.
PATHS = {**REQUIRED_PATHS, "sensing": "/api/v1/sensing/latest"}


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def request_json(url, path, token="", source_address=None):
    import json
    parsed = urlsplit(url)
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = cls(parsed.hostname, parsed.port, timeout=2,
                     source_address=(source_address, 0) if source_address else None)
    try:
        connection.request("GET", path, headers={"Authorization": f"Bearer {token}"} if token else {})
        response = connection.getresponse()
        if response.status != 200:
            return {"error": f"HTTP {response.status}", "http_status": response.status}
        payload = response.read(2_000_001)
        if len(payload) > 2_000_000:
            return {"error": "A RuView válasza túl nagy."}
        def invalid_constant(_):
            raise ValueError("Non-finite JSON")
        value = json.loads(payload, parse_constant=invalid_constant)
        return value if isinstance(value, dict) else {"error": "Érvénytelen RuView-válasz."}
    except (OSError, ValueError, http.client.HTTPException):
        return {"error": "Nem érhető el a RuView. Ellenőrizd a címet és a forrás-IP-t."}
    finally:
        connection.close()


def poll(url, token, source_address, node_id):
    started = time.time()
    replies = {name: request_json(url, path, token, source_address) for name, path in PATHS.items()}
    return normalize(replies, node_id, started, time.time(), time.monotonic_ns())


def normalize(replies, node_id, started, received_at, mono):
    def object_value(value):
        return value if isinstance(value, dict) else {}
    health, nodes, vitals = (replies.get(k, {}) for k in ("health", "nodes", "vitals"))
    processing = object_value(health.get("processing"))
    age = processing.get("last_update_age_ms")
    udp_age = object_value(processing.get("udp")).get("last_datagram_age_ms")
    node_list = nodes.get("nodes") if isinstance(nodes.get("nodes"), list) else []
    node_list = [n for n in node_list if isinstance(n, dict)]
    active = [n for n in node_list if n.get("csi_status") == "active"]
    node = next((n for n in node_list if n.get("node_id") == node_id), None)
    issues = []
    if health.get("source") != "esp32" or (not vitals.get("error") and vitals.get("source") != "esp32"):
        issues.append("physical_source_unconfirmed")
    if processing.get("state") != "live" or not finite_number(age) or age >= 3000 or not finite_number(udp_age) or udp_age >= 3000:
        issues.append("stale")
    if not nodes.get("error") and (not node or node.get("csi_status") != "active" or not finite_number(node.get("csi_last_seen_ms")) or node["csi_last_seen_ms"] >= 3000):
        issues.append("node_unavailable")
    if not nodes.get("error") and (len(active) != 1 or not node or active[0].get("node_id") != node_id):
        issues.append("ambiguous_node")
    if not vitals.get("error") and vitals.get("authority") != "explicit_calibration":
        issues.append("publication_abstained")
    required_replies = [replies.get(name, {}) for name in REQUIRED_PATHS]
    if any(r.get("error") for r in required_replies):
        issues.append("api_auth_required" if any(r.get("http_status") in (401, 403) for r in required_replies) else "api_error")
    if received_at - started > 3:
        issues.append("slow_poll")
    values = object_value(vitals.get("vital_signs"))
    hr = values.get("heart_rate_bpm")
    if not finite_number(hr) or hr <= 0:
        hr = None
    br = values.get("breathing_rate_bpm")
    if not finite_number(br) or br <= 0:
        br = None
    # Public vitals have no per-estimate timestamp/window/profile in this API.
    # Keep nulls, don't invent them from poll time or upstream source constants.
    return {"received_at": received_at, "request_started_at": started,
            "received_monotonic_ns": mono, "poll_duration_s": received_at - started,
            "observed_until": None, "emitted_at": None, "window_seconds": None,
            "source": "ruview_public_vitals", "node_id": node_id, "profile_id": None,
            "hr_bpm": hr if not issues else None, "br_rpm": br if not issues else None,
            "quality_score": values.get("signal_quality"),
            "status": "valid" if not issues and hr is not None else "unavailable",
            "reasons": issues or ([] if hr is not None else ["no_heart_rate"]),
            "abstention_reason": vitals.get("abstention_reason"), "node": node,
            "raw_capture_status": "raw_capture_required", "responses": replies}
