#!/usr/bin/env python3
"""Consume the one-time SSH token handoff without printing or persisting it locally."""
import argparse
import json
import subprocess
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh-control", required=True)
    parser.add_argument("--host", required=True, help="RuView SSH-cél, például user@ruview-host")
    parser.add_argument("--station", default="http://127.0.0.1:8787")
    args = parser.parse_args()
    if args.station != "http://127.0.0.1:8787":
        parser.error("A tokenátadás célja a helyi 8787-es mérőállomás.")
    ssh = ["ssh", "-S", args.ssh_control, "-o", "BatchMode=yes", args.host]
    # The token is captured inside this process, never in a shell argument or tool output.
    remote_read = """python3 - <<'REMOTE'
import os, stat
from pathlib import Path
folder=Path('/dev/shm')/f'ruview-station-auth-{os.getuid()}'
files=list(folder.glob('token-*'))
if len(files)!=1:
    raise SystemExit(4)
info=files[0].lstat()
if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid!=os.getuid():
    raise SystemExit(5)
print(files[0].read_text(),end='')
REMOTE"""
    result = subprocess.run(ssh+[remote_read], capture_output=True, check=False)
    if result.returncode or not result.stdout:
        raise SystemExit("Az egyszeri token még nincs előkészítve. Futtasd az auth helpert az SSH-terminálban.")
    token = result.stdout.decode().strip()
    del result
    base = args.station + "/api"
    try:
        status = json.load(urllib.request.urlopen(base+"/status", timeout=3))
        connection = {**status["connection"], "token": token}
        request = urllib.request.Request(base+"/connection", data=json.dumps(connection).encode(),
                                         headers={"Content-Type":"application/json"}, method="POST")
        urllib.request.urlopen(request, timeout=5).close()
        del connection, token
        for _ in range(12):
            status = json.load(urllib.request.urlopen(base+"/status", timeout=3))
            latest = status.get("latest") or {}
            if latest.get("node") and not any((latest.get("responses") or {}).get(k, {}).get("error") for k in ("nodes", "vitals", "calibration")):
                # This invocation created exactly one file; don't touch unrelated files.
                remote_delete = """python3 - <<'REMOTE'
import os
from pathlib import Path
folder=Path('/dev/shm')/f'ruview-station-auth-{os.getuid()}'
files=list(folder.glob('token-*'))
if len(files)==1:
    files[0].unlink()
    folder.rmdir()
REMOTE"""
                subprocess.run(ssh+[remote_delete], capture_output=True, check=True)
                print(json.dumps({"connected": True, "node_id": latest["node"]["node_id"],
                                  "calibration": latest["responses"]["calibration"].get("status"),
                                  "publication": latest["status"], "temporary_token_deleted": True}, ensure_ascii=False))
                return
            time.sleep(1)
    except (OSError, ValueError, urllib.error.URLError, subprocess.CalledProcessError):
        raise SystemExit("A tokenátadás nem zárult le. A tokent nem írtam ki; ellenőrizd a mérőállomást és az SSH-kapcsolatot.")
    raise SystemExit("A védett API-válasz nem igazolódott. Ellenőrizd a Kapcsolat panelt; az ideiglenes token megmaradt az újrapróbáláshoz.")


if __name__ == "__main__":
    main()
