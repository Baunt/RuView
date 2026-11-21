#!/usr/bin/env python3
"""Read-only, credential-redacted deployment inventory for the RuView update."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def command(args, timeout=20):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip()


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run with sudo on the RuView host.")
    ids = subprocess.check_output(["docker", "ps", "-q", "--filter", "publish=3080"], text=True).split()
    if len(ids) != 1:
        raise SystemExit("The 3080 RuView container is not unique.")
    info = json.loads(subprocess.check_output(["docker", "inspect", ids[0]], text=True))[0]
    if info.get("Path") != "/app/sensing-server":
        raise SystemExit("Unexpected service entry point.")
    image = json.loads(subprocess.check_output(["docker", "image", "inspect", info["Image"]], text=True))[0]
    safe_flags = {"--source", "--tick-ms", "--ui-path", "--http-port", "--ws-port", "--bind-addr",
                  "--data-dir", "--mqtt", "--mqtt-host", "--mqtt-port", "--mqtt-tls", "--mqtt-ca-file",
                  "--mqtt-username", "--mqtt-client-id", "--udp-bind", "--udp-allow", "--udp-tee"}
    args = info.get("Args", [])
    safe_args = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in safe_flags:
            safe_args.append(arg)
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                safe_args.append(args[i + 1])
                i += 1
        else:
            safe_args.append(arg.split("=", 1)[0] + "=<redacted>")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                i += 1
        i += 1
    labels = image.get("Config", {}).get("Labels") or {}
    result = {
        "container_id": ids[0], "name": info.get("Name"), "image_id": info["Image"],
        "configured_image": info["Config"].get("Image"),
        "image_created": image.get("Created"), "image_bytes": image.get("Size"),
        "image_repo_digests": image.get("RepoDigests"),
        "oci_labels": {k: v for k, v in labels.items() if k.startswith("org.opencontainers.image.")},
        "safe_command": [info["Path"], *safe_args],
        "env_names_only": sorted(v.split("=", 1)[0] for v in info["Config"].get("Env", [])),
        "mounts": [{k: v for k, v in m.items() if k in ("Type", "Name", "Source", "Destination", "RW")} for m in info.get("Mounts", [])],
        "port_bindings": info.get("HostConfig", {}).get("PortBindings"),
        "network_mode": info.get("HostConfig", {}).get("NetworkMode"),
        "restart_policy": info.get("HostConfig", {}).get("RestartPolicy"),
        "log_bytes": Path(info["LogPath"]).stat().st_size if info.get("LogPath") and Path(info["LogPath"]).exists() else None,
    }
    for label, args in {
        "binary_version": ["docker", "exec", ids[0], "/app/sensing-server", "--version"],
        "binary_sha256": ["docker", "exec", ids[0], "sha256sum", "/app/sensing-server"],
        "docker_disk_usage": ["docker", "system", "df", "--format", "{{json .}}"],
        "docker_storage": ["docker", "info", "--format", "{{.Driver}} {{.DockerRootDir}}"],
        "rebuildable_cache_sizes": ["du", "-sk", "/var/cache/apt/archives", "/var/lib/docker/buildkit", "/var/log/journal"],
    }.items():
        code, text = command(args, timeout=40)
        result[label] = {"exit_code": code, "output": text}
    code, diff = command(["docker", "diff", ids[0]])
    result["container_code_diff"] = [line for line in diff.splitlines() if "/app/" in line][:100]
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
