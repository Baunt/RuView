#!/usr/bin/env python3
"""One-time bridge for the existing RuView token. Run with sudo on the RuView host.

Reads only the container publishing 3080, drops root before writing a mode-0600
file for the invoking user. Never prints the token or modifies Docker settings.
"""
import json
import os
import pwd
import stat
import subprocess
import tempfile
from pathlib import Path


def main():
    if os.geteuid() != 0 or not os.environ.get("SUDO_UID"):
        raise SystemExit("Futtatás: sudo python3 /dev/shm/ruview-station-balint/auth.py")
    ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "publish=3080"], text=True).split()
    if len(ids) != 1:
        raise SystemExit("Nem azonosítható egyértelműen a 3080-as port RuView-konténere.")
    config = json.loads(subprocess.check_output(["docker", "inspect", ids[0]], text=True))[0]
    if config.get("Path") != "/app/sensing-server":
        raise SystemExit("A konténer nem a várt sensing-server folyamatot futtatja.")
    entries = [v.split("=", 1)[1] for v in config.get("Config", {}).get("Env", []) if v.startswith("RUVIEW_API_TOKEN=")]
    if len(entries) != 1 or not entries[0]:
        raise SystemExit("Nem található beállított RuView API-token.")
    uid = int(os.environ["SUDO_UID"])
    account = pwd.getpwuid(uid)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(uid)
    folder = Path("/dev/shm") / f"ruview-station-auth-{uid}"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = folder.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
        raise SystemExit("A token ideiglenes könyvtárának jogosultsága nem megfelelő.")
    fd, temporary = tempfile.mkstemp(prefix="token-", dir=folder)
    with os.fdopen(fd, "w") as stream:
        stream.write(entries[0])
    print("Az egyszeri tokenátadás előkészítve; a token nem került a kimenetre.")
    print(temporary)


if __name__ == "__main__":
    main()
