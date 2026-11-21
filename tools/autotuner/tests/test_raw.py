import gzip
import json
import struct
import socket
import subprocess
import sys
import time

import pytest

from autotuner.raw import CaptureWriter, SequenceTracker, inspect_capture, parse_frame


def packet(sequence, carriers=256, frequency=2437):
    return struct.pack("<IBBHIIbbBB", 0xC5110001, 1, 1, carriers, frequency, sequence, -62, -95, 0, 0) + bytes([1, 2])*carriers


def test_raw_iq_parser_uses_full_16_bit_carrier_count():
    parsed = parse_frame(packet(100))
    assert parsed["subcarriers"] == 256
    assert parsed["sequence"] == 100
    assert parsed["rssi_dbm"] == -62
    assert parse_frame(packet(100)[:-1]) is None
    assert parse_frame(packet(100, carriers=0)) is None


def test_sequence_wrap_gap_grid_and_backward_are_distinct():
    tracker = SequenceTracker()
    assert tracker.observe(parse_frame(packet(2**32-1)))["segment"] == 0
    assert tracker.observe(parse_frame(packet(0)))["discontinuity"] is None
    assert tracker.observe(parse_frame(packet(3)))["missing_before"] == 2
    assert tracker.observe(parse_frame(packet(3)))["discontinuity"] == "duplicate"
    assert tracker.observe(parse_frame(packet(1)))["discontinuity"] == "sequence_backward_reset_or_reorder"
    assert tracker.observe(parse_frame(packet(2, frequency=2412)))["discontinuity"] == "grid_changed"


def test_capture_preserves_bytes_checksums_and_detects_corruption(tmp_path):
    path = tmp_path / "capture.jsonl.gz"
    writer = CaptureWriter(path)
    for sequence in (10, 12):
        writer.packet(packet(sequence), ("127.0.0.1", 5005))
    writer.packet(b"other packet", ("127.0.0.1", 5005))
    metadata = writer.close()
    result = inspect_capture(path.read_bytes())
    assert result["raw_frames"] == 2
    assert result["datagrams"] == 3
    assert result["missing_sequences"] == 1
    assert result["sha256"] == metadata["sha256"]
    records = [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]
    records[1]["sha256"] = "bad"
    damaged = gzip.compress(b"\n".join(json.dumps(r).encode() for r in records))
    with pytest.raises(ValueError, match="ellenőrzőösszege"):
        inspect_capture(damaged)


def test_unfinished_capture_rejected(tmp_path):
    path = tmp_path / "capture.jsonl.gz"
    writer = CaptureWriter(path)
    writer.packet(packet(1), ("127.0.0.1", 5005))
    writer.file.close()
    with pytest.raises(ValueError):
        inspect_capture(path.read_bytes())


def test_passive_capture_provenance_and_drop_evidence_survive_validation(tmp_path):
    path = tmp_path / 'passive.jsonl.gz'
    context = {'method': 'filtered_af_packet_userspace_receive', 'kernel_arrival_timestamp': None}
    stats = {'packet_socket_drops': 0, 'stopped_reason': 'duration_elapsed'}
    writer = CaptureWriter(path, capture_context=context)
    writer.packet(packet(1), ('192.168.1.117', 5000))
    writer.close(capture_stats=stats)
    checked = inspect_capture(path.read_bytes())
    assert checked['capture_context'] == context
    assert checked['capture_stats'] == stats
    assert checked['device_timestamp'] is None
    assert checked['clock_sync'] == 'unverified'


def test_portable_cli_records_real_loopback_udp_datagrams(tmp_path):
    from pathlib import Path
    output = tmp_path / "udp.jsonl.gz"
    process = subprocess.Popen([sys.executable, str(Path(__file__).parents[1]/"autotuner"/"raw.py"),
                               "--port", "0", "--seconds", "0.2", "--output", str(output)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready["status"] == "recording"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(packet(1), ("127.0.0.1", ready["port"]))
            sender.sendto(packet(3), ("127.0.0.1", ready["port"]))
        process.communicate(timeout=5)
        assert process.returncode == 0
        evidence = inspect_capture(output.read_bytes())
        assert evidence["raw_frames"] == 2
        assert evidence["missing_sequences"] == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
