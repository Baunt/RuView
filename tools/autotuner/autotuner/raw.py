"""Portable, standard-library UDP tee recorder. Run in RuView's network namespace."""
import argparse
import base64
import gzip
import hashlib
import ipaddress
import json
import signal
import socket
import struct
import time
from pathlib import Path


def parse_frame(payload):
    if len(payload) < 20 or payload[:4] != b"\x01\x00\x11\xc5":
        return None
    _, node, antennas, carriers, frequency, sequence, rssi, noise, ppdu, flags = struct.unpack_from("<IBBHIIbbBB", payload)
    if not 1 <= antennas <= 8 or not 1 <= carriers <= 2048 or len(payload) < 20 + antennas*carriers*2:
        return None
    return {"node_id": node, "antennas": antennas, "subcarriers": carriers,
            "frequency_mhz": frequency, "sequence": sequence, "rssi_dbm": -abs(rssi),
            "noise_floor_dbm": noise, "ppdu_type": ppdu, "flags": flags,
            "iq_bytes": antennas*carriers*2}


class SequenceTracker:
    def __init__(self):
        self.nodes = {}

    def observe(self, frame):
        node, sequence = frame["node_id"], frame["sequence"]
        grid = tuple(frame[k] for k in ("antennas", "subcarriers", "frequency_mhz", "ppdu_type", "flags"))
        previous = self.nodes.get(node)
        segment, missing, reason = (previous[2] if previous else 0), 0, None
        if not previous:
            reason = "first_frame"
        elif previous[1] != grid:
            segment += 1
            reason = "grid_changed"
        else:
            delta = (sequence - previous[0]) % (2**32)
            if delta == 0:
                reason = "duplicate"
            elif delta >= 2**31:
                segment += 1
                reason = "sequence_backward_reset_or_reorder"
            elif delta > 1:
                missing = delta - 1
                segment += 1
                reason = "sequence_gap"
        self.nodes[node] = (sequence, grid, segment)
        return {"segment": segment, "missing_before": missing, "discontinuity": reason}


class CaptureWriter:
    def __init__(self, path, *, capture_context=None):
        self.path = Path(path)
        self.file = gzip.open(self.path, "xt", encoding="utf-8")
        self.path.chmod(0o600)
        self.tracker = SequenceTracker()
        self.count = self.valid = self.missing = self.duplicates = 0
        self.start = time.time()
        self.anchor = (self.start, time.monotonic_ns())
        self.clock_jumps = 0
        self.first_raw_at = self.last_raw_at = None
        self.capture_context = capture_context
        self.write({"type": "header", "format": "ruview-raw-v1", "utc_anchor": self.anchor[0],
                    "monotonic_anchor_ns": self.anchor[1], "host": socket.gethostname(),
                    "device_timestamp": None, "clock_sync": "unverified",
                    **({"capture_context": capture_context} if capture_context is not None else {})})

    def write(self, record):
        self.file.write(json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n")

    def packet(self, payload, peer, utc=None, mono=None):
        utc = time.time() if utc is None else utc
        mono = time.monotonic_ns() if mono is None else mono
        frame = parse_frame(payload)
        sequence = self.tracker.observe(frame) if frame else None
        clock_error = utc - (self.anchor[0] + (mono-self.anchor[1])/1e9)
        jumped = abs(clock_error) > .1
        if jumped:
            self.clock_jumps += 1
            self.anchor = (utc, mono)
        self.write({"type": "datagram", "received_at": utc, "received_monotonic_ns": mono,
                    "peer": list(peer), "payload_b64": base64.b64encode(payload).decode(),
                    "sha256": hashlib.sha256(payload).hexdigest(), "frame": frame,
                    "sequence": sequence, "clock_jump": jumped, "clock_error_s": clock_error})
        self.count += 1
        if frame:
            self.valid += 1
            self.first_raw_at = utc if self.first_raw_at is None else self.first_raw_at
            self.last_raw_at = utc
            self.missing += sequence["missing_before"]
            self.duplicates += sequence["discontinuity"] == "duplicate"

    def close(self, *, capture_stats=None):
        self.write({"type": "footer", "datagrams": self.count, "raw_frames": self.valid,
                    "missing_sequences": self.missing, "duplicates": self.duplicates,
                    "clock_jumps": self.clock_jumps, "started_at": self.start, "ended_at": time.time(),
                    **({"capture_stats": capture_stats} if capture_stats is not None else {})})
        self.file.close()
        metadata = {"format": "ruview-raw-v1", "file": self.path.name,
                    "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                    "raw_frames": self.valid, "datagrams": self.count,
                    "first_raw_at": self.first_raw_at, "last_raw_at": self.last_raw_at,
                    "missing_sequences": self.missing, "clock_jumps": self.clock_jumps,
                    "device_timestamp": None, "clock_sync": "unverified"}
        if self.capture_context is not None:
            metadata["capture_context"] = self.capture_context
        if capture_stats is not None:
            metadata["capture_stats"] = capture_stats
        manifest_path = self.path.with_suffix(self.path.suffix + ".manifest.json")
        manifest_path.write_text(json.dumps(metadata, indent=2))
        manifest_path.chmod(0o600)
        return metadata


def inspect_capture(payload):
    """Validate content and rebuild sequence evidence; don't trust uploaded summaries."""
    import io
    tracker, nodes, count, valid, missing, jumps = SequenceTracker(), set(), 0, 0, 0, 0
    header, footer, total_bytes, first, last = None, None, 0, None, None
    previous_mono = None
    with gzip.GzipFile(fileobj=io.BytesIO(payload)) as stream:
        while line := stream.readline(200_001):
            total_bytes += len(line)
            if len(line) > 200_000 or total_bytes > 256_000_000:
                raise ValueError("A nyers felvétel túl nagy.")
            record = json.loads(line)
            if header is None:
                if record.get("type") != "header" or record.get("format") != "ruview-raw-v1":
                    raise ValueError("Ismeretlen nyers felvételi formátum.")
                header = record
                continue
            if footer is not None:
                raise ValueError("Adat található a lezárás után.")
            if record.get("type") == "footer":
                footer = record
                continue
            if record.get("type") != "datagram":
                raise ValueError("Ismeretlen rekord a felvételben.")
            raw = base64.b64decode(record["payload_b64"], validate=True)
            if hashlib.sha256(raw).hexdigest() != record["sha256"]:
                raise ValueError("A datagram ellenőrzőösszege eltér.")
            mono = record["received_monotonic_ns"]
            if previous_mono is not None and mono < previous_mono:
                raise ValueError("A monotonic fogadási idő visszalép.")
            previous_mono = mono
            stamp = record["received_at"]
            if not isinstance(stamp, (int, float)) or not 0 < stamp < 10**11:
                raise ValueError("Érvénytelen fogadási idő.")
            count += 1
            jumps += bool(record.get("clock_jump"))
            frame = parse_frame(raw)
            if frame:
                valid += 1
                nodes.add(frame["node_id"])
                missing += tracker.observe(frame)["missing_before"]
                first = stamp if first is None else first
                last = stamp
    if not footer or footer.get("datagrams") != count or not valid:
        raise ValueError("A felvétel nincs lezárva, vagy nem tartalmaz nyers I/Q-keretet.")
    result = {"format": "ruview-raw-v1", "sha256": hashlib.sha256(payload).hexdigest(),
            "datagrams": count, "raw_frames": valid, "missing_sequences": missing,
            "node_ids": sorted(nodes), "clock_jumps": jumps, "first_raw_at": first,
            "last_raw_at": last, "clock_sync": "unverified", "device_timestamp": None,
            "host": header.get("host")}
    if "capture_context" in header:
        result["capture_context"] = header["capture_context"]
    if "capture_stats" in footer:
        result["capture_stats"] = footer["capture_stats"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5007)
    parser.add_argument("--seconds", type=float, default=1200)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not ipaddress.ip_address(args.bind).is_loopback or not 0 < args.seconds <= 28800:
        parser.error("Loopback cím és 0–28800 másodperces időtartam szükséges.")
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    sock = socket.socket(socket.AF_INET6 if ":" in args.bind else socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(.5)
    writer = CaptureWriter(args.output)
    print(json.dumps({"status": "recording", "bind": args.bind, "port": sock.getsockname()[1]}), flush=True)
    deadline = time.monotonic() + args.seconds
    try:
        while running and time.monotonic() < deadline:
            try:
                payload, peer = sock.recvfrom(65535)
                writer.packet(payload, peer)
            except socket.timeout:
                continue
    finally:
        sock.close()
        print(json.dumps(writer.close(), indent=2))


if __name__ == "__main__":
    main()
