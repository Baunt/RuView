#!/usr/bin/env python3
"""Summarize an unchanged passive ESP32 PCAP and its read-only API snapshots."""
import argparse
import collections
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autotuner.pcap import udp_datagrams
from autotuner.raw import parse_frame


def save_original_or_equal(path, text):
    if path.exists():
        if path.read_text() != text:
            raise ValueError(f"Refusing to replace an existing different artifact: {path.name}")
    else:
        with path.open("x") as stream:
            stream.write(text)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    folder = args.folder
    pcap = (folder / "esp32.pcap").read_bytes()
    magics = collections.Counter()
    grids = collections.defaultdict(list)
    raw = []
    datagrams = 0
    for packet in udp_datagrams(pcap):
        if packet["source_ip"] != "192.168.1.117" or packet["destination_port"] != 5005:
            raise ValueError("A packet is outside the configured ESP32 capture filter")
        payload = packet["payload"]
        datagrams += 1
        magics[payload[:4].hex()] += 1
        frame = parse_frame(payload)
        if frame is None:
            continue
        iq = [v if v < 128 else v - 256 for v in payload[20:20 + frame["iq_bytes"]]]
        amplitudes = [math.hypot(iq[k], iq[k + 1]) for k in range(0, len(iq), 2)]
        frame = {**frame, "received_at": packet["received_at"],
                 "mean_amplitude": statistics.mean(amplitudes)}
        key = tuple(frame[k] for k in ("node_id", "antennas", "subcarriers", "frequency_mhz", "ppdu_type", "flags"))
        grids[key].append(frame)
        raw.append((frame, payload))
    grid_stats = []
    for key, frames in grids.items():
        stamps = [f["received_at"] for f in frames]
        span = stamps[-1] - stamps[0]
        grid_stats.append({"grid": dict(zip(("node_id", "antennas", "subcarriers", "frequency_mhz", "ppdu_type", "flags"), key)),
                           "frames": len(frames), "span_s": span,
                           "observed_rate_hz": (len(frames) - 1) / span if span > 0 else None,
                           "rssi_dbm_counts": dict(collections.Counter(f["rssi_dbm"] for f in frames)),
                           "median_rssi_dbm": statistics.median(f["rssi_dbm"] for f in frames),
                           "mean_amplitude": statistics.mean(f["mean_amplitude"] for f in frames)})
    deltas = collections.Counter((b[0]["sequence"] - a[0]["sequence"]) % (2**32) for a, b in zip(raw, raw[1:]))
    snapshots = [json.loads(line) for line in (folder / "api-observations.jsonl").read_text().splitlines()]
    calibrated = collections.Counter()
    node_counts = collections.Counter()
    for snapshot in snapshots:
        replies = snapshot["responses"]
        evidence = replies.get("sensing", {}).get("calibrated_presence_evidence")
        calibrated[str(evidence.get("person_count")) if evidence else "unavailable"] += 1
        for node in replies.get("nodes", {}).get("nodes", []):
            if node.get("node_id") == 1:
                node_counts[str(node.get("person_count"))] += 1
    summary = {"schema": "ruview.autotuner.passive-analysis.v1", "pcap_sha256": hashlib.sha256(pcap).hexdigest(),
               "datagrams": datagrams, "udp_payload_magic_bytes": dict(magics),
               "raw_frames": len(raw), "grids": grid_stats, "raw_sequence_delta_counts": dict(deltas),
               "api_samples": len(snapshots), "node_person_counts": dict(node_counts),
               "calibrated_presence_person_counts": dict(calibrated),
               "public_vitals_authority": dict(collections.Counter(s["responses"].get("vitals", {}).get("authority") for s in snapshots)),
               "notes": ["Sequence gaps alone are not proof of network loss.",
                         "PCAP contains host realtime timestamps, no per-packet monotonic clock.",
                         "This format is not yet an attached raw-v1 collector capture."]}
    tsv = "".join(f"{frame['received_at']:.9f}\t{frame['subcarriers']}\t{payload.hex()}\n" for frame, payload in raw)
    save_original_or_equal(folder / "raw-frames.tsv", tsv)
    save_original_or_equal(folder / "passive-analysis.json", json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
