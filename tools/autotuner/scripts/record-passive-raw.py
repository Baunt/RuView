#!/usr/bin/env python3
"""Record only ESP32 UDP ingress through a private Linux packet socket.

Sudo is used to open/filter the socket, then privileges are dropped. No service,
network setting, packet forwarding, or firewall rule is modified. Timestamps are
actual userspace socket-receive UTC and monotonic readings, not inferred PCAP times.
Deploy alongside raw.py and pcap.py on the RuView host.
"""
import argparse
import ctypes
import json
import os
import pwd
import shutil
import signal
import socket
import struct
import subprocess
import time
from pathlib import Path

from pcap import ethernet_udp
from raw import CaptureWriter


class Instruction(ctypes.Structure):
    _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint32)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ushort), ("instructions", ctypes.POINTER(Instruction))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 900 or os.geteuid() != 0 or not os.environ.get("SUDO_UID"):
        parser.error("Sudo és 1–900 másodperces időtartam szükséges.")
    if shutil.disk_usage("/dev/shm").free < 200_000_000:
        raise SystemExit("Nincs elegendő szabad RAM-alapú tárhely.")
    route = json.loads(subprocess.check_output(["ip", "-json", "route", "get", "192.168.1.117"], text=True))
    if len(route) != 1 or route[0].get("dev") != "eth0":
        raise SystemExit("A várt eth0 interfész nem igazolható.")
    packet_filter = "ip and udp and src host 192.168.1.117 and dst port 5005"
    compiled = subprocess.check_output(["tcpdump", "-ddd", "-s", "65535", "-y", "EN10MB", packet_filter],
                                       text=True, timeout=5).splitlines()
    count = int(compiled[0])
    if not 1 <= count <= 4096 or len(compiled) != count + 1:
        raise SystemExit("A socket-szűrő nem igazolható.")
    instructions = (Instruction * count)(*(Instruction(*map(int, line.split())) for line in compiled[1:]))
    program = Program(count, instructions)
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.bind(("eth0", 0))
    sock.setsockopt(socket.SOL_SOCKET, 26, bytes(program))  # SO_ATTACH_FILTER, Linux socket.h
    sock.setsockopt(263, 23, 1)  # SOL_PACKET, PACKET_IGNORE_OUTGOING
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4_000_000)
    sock.settimeout(.5)
    account = pwd.getpwuid(int(os.environ["SUDO_UID"]))
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.umask(0o077)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    writer = CaptureWriter(args.output, capture_context={
        "method": "filtered_af_packet_userspace_receive", "interface": "eth0",
        "filter": packet_filter, "packet_direction": "ingress",
        "timestamp_definition": "UTC and monotonic read immediately after socket recv",
        "kernel_arrival_timestamp": None, "ruview_settings_changed": False,
        "seconds_requested": args.seconds})
    started = time.monotonic()
    deadline = started + args.seconds
    reason = "duration_elapsed"
    print(json.dumps({"event": "recording", "path": str(args.output), "seconds": args.seconds}), flush=True)
    try:
        while running and time.monotonic() < deadline:
            try:
                packet = sock.recv(262144)
                utc, mono = time.time(), time.monotonic_ns()
            except socket.timeout:
                continue
            decoded = ethernet_udp(packet)
            if decoded is None or decoded["source_ip"] != "192.168.1.117" or decoded["destination_port"] != 5005:
                raise ValueError("A socket-szűrőn kívüli csomag érkezett.")
            writer.packet(decoded["payload"], (decoded["source_ip"], decoded["source_port"]), utc, mono)
            if writer.file.tell() >= 128_000_000 or writer.count >= 60000:
                reason = "size_limit"
                break
        if not running:
            reason = "operator_stop"
    except BaseException:
        reason = "error"
        raise
    finally:
        stats = struct.unpack("II", sock.getsockopt(263, 6, 8))  # PACKET_STATISTICS
        sock.close()
        result = writer.close(capture_stats={"packet_socket_packets": stats[0], "packet_socket_drops": stats[1],
                                             "stopped_reason": reason, "observed_duration_s": time.monotonic() - started})
        print(json.dumps({"event": "closed", **result}), flush=True)
    return 1 if reason == "size_limit" else 0


if __name__ == "__main__":
    raise SystemExit(main())
