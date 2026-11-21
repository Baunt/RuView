import struct

import pytest


def crc16(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (0xA001 if crc & 1 else 0)
    return crc


@pytest.fixture
def fit_bytes():
    # Synthetic protocol fixture only. Never a physiological validation recording.
    def make(records):
        data = b"\x40\x00\x00\x14\x00\x02\xfd\x04\x86\x03\x01\x02"
        for stamp, hr in records:
            data += b"\x00" + struct.pack("<IB", stamp, hr)
        header = struct.pack("<BBHI4s", 14, 0x20, 2100, len(data), b".FIT")
        header += struct.pack("<H", crc16(header))
        content = header + data
        return content + struct.pack("<H", crc16(content))
    return make


@pytest.fixture
def replies():
    return {"health": {"source": "esp32", "processing": {"state": "live", "last_update_age_ms": 12,
             "udp": {"last_datagram_age_ms": 13}}},
            "nodes": {"nodes": [{"node_id": 1, "csi_status": "active", "csi_last_seen_ms": 15}]},
            "calibration": {"status": "ready"},
            "vitals": {"source": "esp32", "authority": "explicit_calibration", "vital_signs": {"heart_rate_bpm": 72, "breathing_rate_bpm": 14, "signal_quality": .8}}}
