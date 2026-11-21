"""Identity-bound empty-room calibration, using only the server's readiness gates."""
import http.client
import json
import re
import time

IDENTITY_FIELDS = ("boot_epoch", "session_id", "binding_digest", "source_node_ids")


class CalibrationError(RuntimeError):
    pass


class LocalRuView:
    """Run on the RuView host; the existing token never leaves process memory."""
    def __init__(self, token, port=3080):
        self.token, self.port = token, port

    def __call__(self, path, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            headers = {"Authorization": f"Bearer {self.token}"}
            if body is not None:
                headers["Content-Type"] = "application/json"
            connection.request("GET" if body is None else "POST", path,
                               body=None if body is None else json.dumps(body), headers=headers)
            response = connection.getresponse()
            payload = response.read(2_000_001)
            if response.status != 200 or len(payload) > 2_000_000:
                raise CalibrationError(f"RuView API HTTP {response.status}")
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise CalibrationError("Invalid RuView response")
            return data
        except (OSError, ValueError, http.client.HTTPException):
            raise CalibrationError("A RuView kalibrációs API nem érhető el.") from None
        finally:
            connection.close()


def identity(value):
    return {key: value.get(key) for key in IDENTITY_FIELDS}


def source_ready(api, node_id):
    health = api("/health")
    proc = health.get("processing") or {}
    if health.get("source") != "esp32" or proc.get("state") != "live" or not isinstance(proc.get("last_update_age_ms"), (int, float)) or proc["last_update_age_ms"] >= 3000:
        raise CalibrationError("Nincs friss fizikai ESP32-adatfolyam.")
    nodes = api("/api/v1/nodes").get("nodes", [])
    active = [n for n in nodes if n.get("csi_status") == "active"]
    if len(active) != 1 or active[0].get("node_id") != node_id or not isinstance(active[0].get("csi_last_seen_ms"), (int, float)) or active[0]["csi_last_seen_ms"] >= 3000:
        raise CalibrationError("A kijelölt egyetlen CSI-érzékelő nem azonosítható friss forrásként.")


def reset_for_relocation(api, expected_previous, new_binding_digest, node_id, *,
                         confirmed_empty=False, confirmed_relocated=False,
                         emit=lambda value: None):
    """Replace only the snapshotted model after an operator-confirmed move.

    This is intentionally separate from ordinary calibration: fresh models
    remain protected unless both the move and the empty apartment are confirmed.
    The archived receipt is evidence, not a restorable copy of model weights.
    """
    if confirmed_empty is not True or confirmed_relocated is not True:
        raise CalibrationError("Az áthelyezést és az üres lakást külön meg kell erősíteni.")
    if (not re.fullmatch(r"[0-9a-f]{64}", new_binding_digest)
            or new_binding_digest == expected_previous.get("binding_digest")
            or not expected_previous.get("model_id")
            or any(expected_previous.get(key) is None for key in IDENTITY_FIELDS)
            or expected_previous.get("source_node_ids") != [node_id]):
        raise CalibrationError("Új elhelyezési kötés és teljes korábbi modellazonosság szükséges.")
    source_ready(api, node_id)
    current = api("/api/v1/calibration/status")
    if (current.get("status") == "none" and not current.get("active")
            and current.get("model_id") is None and current.get("session_id") is None):
        emit({"event": "relocation_reset_not_needed", "status": current})
        return
    if (current.get("status") not in ("fresh", "stale", "expired")
            or identity(current) != identity(expected_previous)
            or current.get("model_id") != expected_previous["model_id"]):
        raise CalibrationError("Másik vagy folyamatban lévő modell törlését nem engedem.")
    emit({"event": "previous_relocation_calibration", "status": current,
          "new_binding_digest": new_binding_digest})
    result = api("/api/v1/calibration/reset", identity(current))
    if result.get("success") is not True:
        raise CalibrationError("Az áthelyezés előtti modell visszaállítása nem sikerült.")
    after = api("/api/v1/calibration/status")
    if (after.get("status") != "none" or after.get("active")
            or after.get("model_id") is not None or after.get("session_id") is not None):
        raise CalibrationError("A törlés utáni üres modellállapot visszaolvasása nem igazolódott.")
    emit({"event": "relocation_reset_verified", "result": result, "status": after})


def calibrate(api, binding_digest, node_id, *, confirmed_empty=False, emit=lambda value: None,
              timeout_s=900, interval_s=2, sleep=time.sleep, monotonic=time.monotonic,
              start_delay_s=0):
    if not confirmed_empty:
        raise CalibrationError("Az üres szoba állapotát előbb az operátornak kell megerősítenie.")
    if not re.fullmatch(r"[0-9a-f]{64}", binding_digest) or not 0 <= node_id <= 255:
        raise CalibrationError("Hibás szoba- vagy érzékelőazonosító.")
    if not isinstance(start_delay_s, int) or not 0 <= start_delay_s <= 300:
        raise CalibrationError("Az indulási késleltetés 0–300 egész másodperc lehet.")
    source_ready(api, node_id)
    previous = api("/api/v1/calibration/status")
    previous_status = previous.get("status")
    # `active` means a model exists, including a completed stale one. The
    # server permits stale/expired renewal but protects fresh/collecting models.
    if previous_status not in ("none", "expired", "stale") or (
        previous_status == "none" and previous.get("active")
    ):
        raise CalibrationError("Már létezik aktív vagy folyamatban lévő kalibráció; nem írom felül.")
    if previous_status in ("stale", "expired"):
        emit({"event": "previous_calibration", "status": previous})
    if start_delay_s:
        emit({"event": "waiting_for_empty", "start_delay_s": start_delay_s,
              "message": "Hagyd el a lakást az indulás előtt. A gyűjtés még nem kezdődött el."})
        sleep(start_delay_s)
        source_ready(api, node_id)
    request = {"binding_digest": binding_digest, "source_node_ids": [node_id]}
    start = api(f"/api/v1/calibration/start?source_node_id={node_id}", request)
    if start.get("success") is not True:
        raise CalibrationError("Kalibráció nem indult: " + str(start.get("error_code") or start.get("error") or "ismeretlen válasz"))
    expected = identity(start)
    if any(expected[k] is None for k in IDENTITY_FIELDS) or expected["binding_digest"] != binding_digest or expected["source_node_ids"] != [node_id]:
        raise CalibrationError("A szerver kalibrációs azonossága nem igazolható.")
    emit({"event": "started", **expected, "source_grid": start.get("source_grid")})
    finished = False
    deadline = monotonic() + timeout_s
    try:
        while monotonic() < deadline:
            status = api("/api/v1/calibration/status")
            if identity(status) != expected:
                raise CalibrationError("Megváltozott a kalibráció boot/session/szoba/érzékelő azonossága.")
            if status.get("sequence_fault_node_ids"):
                raise CalibrationError("Sorszámfolytonossági hiba; a kalibráció nem zárható le.")
            source_ready(api, node_id)
            frames, need = status.get("frame_count", 0), status.get("min_frames", 0)
            elapsed, duration = status.get("elapsed_s", 0), status.get("min_duration_s", 0)
            emit({"event": "progress", "frame_count": frames, "min_frames": need,
                  "elapsed_s": elapsed, "min_duration_s": duration, "status": status.get("status")})
            if need > 0 and frames >= need and elapsed >= duration and not status.get("missing_source_node_ids"):
                result = api("/api/v1/calibration/stop", expected)
                if result.get("success") is not True:
                    raise CalibrationError("A szerver nem fogadta el a lezárást: " + str(result.get("error_code") or result.get("error")))
                receipt = result.get("model_receipt") or {}
                if identity(receipt) != expected or not receipt.get("model_id") or receipt.get("model_id") != result.get("model_id"):
                    raise CalibrationError("A kalibrációs receipt nem egyezik a kért azonossággal.")
                finished = True
                verified = api("/api/v1/calibration/status")
                if identity(verified) != expected or verified.get("model_id") != receipt["model_id"] or verified.get("status") != "fresh":
                    raise CalibrationError("A lezárt modell friss állapota nem igazolódott.")
                emit({"event": "completed", "result": result, "verified_status": verified})
                return result
            sleep(interval_s)
        raise CalibrationError("A kalibráció elérte az időkorlátot; nem állítom késznek.")
    finally:
        if not finished:
            try:
                current = api("/api/v1/calibration/status")
                if identity(current) == expected and current.get("status") in ("collecting", "uncalibrated"):
                    cancellation = api("/api/v1/calibration/cancel", expected)
                    emit({"event": "cancelled", "success": cancellation.get("success") is True})
            except Exception:
                emit({"event": "needs_attention", "reason": "Az el nem készült kalibráció megszakítása nem igazolható."})
