#!/usr/bin/env python3
"""Identity-bound empty-room calibration on the RuView host; token stays in memory."""
import argparse
import json
import os
import pwd
import subprocess
import sys
import uuid
from pathlib import Path

from calibration import CalibrationError, LocalRuView, calibrate, reset_for_relocation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirmed-empty", action="store_true", required=True)
    parser.add_argument("--leave-delay-seconds", type=int, default=0,
                        help="0–300 s késleltetés a lakás elhagyásához; ez még nem kalibrációs gyűjtés.")
    parser.add_argument("--binding-file", type=Path)
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--relocation-from", type=Path,
                        help="Az elvárt korábbi modell státusz-pillanatképe, csak áthelyezéshez.")
    parser.add_argument("--confirmed-relocated", action="store_true")
    args = parser.parse_args()
    if bool(args.relocation_from) != args.confirmed_relocated:
        parser.error("Az áthelyezéshez a --relocation-from és --confirmed-relocated együtt szükséges.")
    if os.geteuid() != 0 or not os.environ.get("SUDO_UID"):
        raise SystemExit("A Docker-beállítás olvasásához sudo szükséges.")
    ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "publish=3080"], text=True).split()
    if len(ids) != 1:
        raise SystemExit("A RuView-konténer nem azonosítható egyértelműen.")
    info = json.loads(subprocess.check_output(["docker", "inspect", ids[0]], text=True))[0]
    if info.get("Path") != "/app/sensing-server":
        raise SystemExit("A konténer nem a várt RuView szerver.")
    values = [v.split("=", 1)[1] for v in info.get("Config", {}).get("Env", []) if v.startswith("RUVIEW_API_TOKEN=")]
    if len(values) != 1 or not values[0]:
        raise SystemExit("A RuView-token nem olvasható.")
    api = LocalRuView(values[0])
    del info, values
    account = pwd.getpwuid(int(os.environ["SUDO_UID"]))
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)
    folder = Path(__file__).resolve().parent
    binding = json.loads((args.binding_file or folder / "room-binding.json").read_text())
    environment_file = args.environment_file or folder / "room-environment.json"
    environment = json.loads(environment_file.read_text()) if environment_file.exists() else None
    if environment is not None and (
        environment.get("schema") != "ruview.autotuner.room-environment.v1"
        or environment.get("binding_digest") != binding["binding_digest"]
    ):
        raise SystemExit("A környezeti leírás nem a jelenlegi szobakötéshez tartozik.")
    output = folder / "calibration-progress.jsonl"
    if output.exists():
        output.rename(folder / f"calibration-previous-{uuid.uuid4()}.jsonl")
    with output.open("x", encoding="utf-8") as stream:
        output.chmod(0o600)
        def emit(value):
            encoded = json.dumps(value, ensure_ascii=False)
            stream.write(encoded + "\n")
            stream.flush()
            print(encoded, flush=True)
        try:
            if environment is not None:
                scope = environment.get("empty_confirmation_scope", "measurement_room_only")
                emit({"event": "environment_context", "environment": environment,
                      "notice": ("Az üres állapot a teljes saját lakásra vonatkozik. " if scope == "whole_own_apartment" else "Az üres állapot a mérőhelyiségre vonatkozik. ")
                                + "A szomszédos lakások mozgása nem kontrollált; hatása külön ellenőrzendő."})
            if args.relocation_from:
                expected = json.loads(args.relocation_from.read_text())
                reset_for_relocation(api, expected, binding["binding_digest"], binding["source_node_ids"][0],
                                     confirmed_empty=args.confirmed_empty,
                                     confirmed_relocated=args.confirmed_relocated, emit=emit)
            calibrate(api, binding["binding_digest"], binding["source_node_ids"][0],
                      confirmed_empty=args.confirmed_empty, emit=emit,
                      start_delay_s=args.leave_delay_seconds)
        except (CalibrationError, KeyboardInterrupt) as exc:
            emit({"event": "error", "message": str(exc) or "A felhasználó megszakította."})
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
