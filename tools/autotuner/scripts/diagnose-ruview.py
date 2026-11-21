#!/usr/bin/env python3
"""Bounded read-only API and ESP32 packet capture on the RuView host.

Run with sudo. Only the existing container token is read; it stays in memory.
No RuView, firmware, firewall, router, or calibration settings are changed.
"""
import argparse
import collections
import json
import os
import pwd
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from calibration import CalibrationError, LocalRuView

PATHS = {
    "health": "/health",
    "nodes": "/api/v1/nodes",
    "calibration": "/api/v1/calibration/status",
    "vitals": "/api/v1/vital-signs",
    "sensing": "/api/v1/sensing/latest",
    "edge_vitals": "/api/v1/edge-vitals",
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    if not 5 <= args.seconds <= 120:
        parser.error("5–120 másodperces diagnosztika engedélyezett.")
    if os.geteuid() != 0 or not os.environ.get("SUDO_UID"):
        raise SystemExit("Futtatás sudo-val, a RuView-hoston.")
    tcpdump, timeout = shutil.which("tcpdump"), shutil.which("timeout")
    if not tcpdump or not timeout or shutil.disk_usage("/dev/shm").free < 20_000_000:
        raise SystemExit("A korlátozott passzív rögzítés feltételei nem teljesülnek.")
    account = pwd.getpwuid(int(os.environ["SUDO_UID"]))
    route = json.loads(subprocess.check_output(["ip", "-json", "route", "get", "192.168.1.117"], text=True))
    if len(route) != 1 or route[0].get("dev") != "eth0":
        raise SystemExit("A várt eth0 interfész nem igazolható.")
    ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "publish=3080"], text=True).split()
    if len(ids) != 1:
        raise SystemExit("A RuView-konténer nem azonosítható egyértelműen.")
    config = json.loads(subprocess.check_output(["docker", "inspect", ids[0]], text=True))[0]
    if config.get("Path") != "/app/sensing-server":
        raise SystemExit("Nem a várt RuView-konténer.")
    values = [v.split("=", 1)[1] for v in config.get("Config", {}).get("Env", []) if v.startswith("RUVIEW_API_TOKEN=")]
    if len(values) != 1 or not values[0]:
        raise SystemExit("A meglévő RuView-token nem olvasható.")
    api = LocalRuView(values[0])
    del config, values
    base = Path(__file__).resolve().parent
    info = base.stat()
    if base.is_symlink() or info.st_uid != account.pw_uid or info.st_mode & 0o077:
        raise SystemExit("Nem megfelelő privát diagnosztikai könyvtár.")
    folder = base / ("diagnostic-" + str(uuid.uuid4()))
    folder.mkdir(mode=0o700)
    os.chown(folder, account.pw_uid, account.pw_gid)
    packet_filter = "udp and src host 192.168.1.117 and dst port 5005"
    command = [timeout, "--signal=INT", "--kill-after=3", str(args.seconds),
               tcpdump, "-i", "eth0", "-nn", "-s", "0", "-U",
               "--time-stamp-precision=nano", "-Z", account.pw_name,
               "-w", str(folder / "esp32.pcap"), packet_filter]
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    os.umask(0o077)
    context = {"schema": "ruview.autotuner.passive-diagnostic.v1",
               "interface": "eth0", "filter": packet_filter,
               "seconds_requested": args.seconds, "host_clock": "server_host",
               "scene": "not_operator_confirmed_for_this_diagnostic",
               "packet_timestamp": "pcap_host_realtime_nanoseconds",
               "packet_monotonic_timestamp": None, "clock_sync": "unverified",
               "capture_scope": "ESP32 UDP ingress before Docker forwarding",
               "ruview_configuration_changed": False,
               "token_persisted": False}
    (folder / "context.json").write_text(json.dumps(context, indent=2) + "\n")
    print(json.dumps({"event": "started", "folder": str(folder)}), flush=True)
    deadline = time.monotonic() + args.seconds
    samples = []
    with (folder / "api-observations.jsonl").open("x") as stream:
        while time.monotonic() < deadline:
            row = {"request_started_at": time.time(), "responses": {}}
            for name, path in PATHS.items():
                try:
                    row["responses"][name] = api(path)
                except CalibrationError as exc:
                    row["responses"][name] = {"error": str(exc)}
            row.update(received_at=time.time(), received_monotonic_ns=time.monotonic_ns())
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            samples.append(row)
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    _, errors = child.communicate(timeout=10)
    (folder / "capture-stats.txt").write_bytes(errors)
    pcap = folder / "esp32.pcap"
    if child.returncode not in (0, 124) or not pcap.exists() or pcap.stat().st_size <= 24:
        raise SystemExit("A passzív felvétel sikere nem igazolható; az API-diagnosztika megmaradt.")
    pcap.chmod(0o600)
    calibrated = collections.Counter()
    raw_node = collections.Counter()
    missing = 0
    for row in samples:
        replies = row["responses"]
        for node in replies.get("nodes", {}).get("nodes", []):
            if node.get("node_id") == 1:
                raw_node[str(node.get("person_count"))] += 1
        evidence = replies.get("sensing", {}).get("calibrated_presence_evidence")
        if evidence is None:
            missing += 1
        else:
            calibrated[str(evidence.get("person_count"))] += 1
    summary = {"event": "completed", "folder": str(folder), "api_samples": len(samples),
               "pcap_bytes": pcap.stat().st_size, "capture_returncode": child.returncode,
               "node_person_counts": dict(raw_node),
               "calibrated_presence_person_counts": dict(calibrated),
               "missing_calibrated_evidence_samples": missing}
    (folder / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)


if __name__ == "__main__":
    main()
