#!/usr/bin/env python3
"""Audit iSCSI LUNs on the Synology NAS against Kubernetes PVs.

Categorizes LUNs into:
- Bound: LUN matches an active Bound PV in K8s
- Released: LUN matches a Released/Available PV (reclaimable)
- Orphaned: LUN has no corresponding K8s PV at all (safe to delete)

Environment variables:
    SYNOLOGY_USER     - SSH username (default: manager)
    SYNOLOGY_HOST     - NAS IP (default: 192.168.5.8)
    SYNOLOGY_PASSWORD - SSH password (required)
"""

import json
import os
import subprocess
import sys

import paramiko


def get_ssh_client():
    ip = os.environ.get("SYNOLOGY_HOST", "192.168.5.8")
    user = os.environ.get("SYNOLOGY_USER", "manager")
    password = os.environ.get("SYNOLOGY_PASSWORD")
    if not password:
        print("ERROR: SYNOLOGY_PASSWORD not set")
        sys.exit(1)
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username=user, password=password)
    return client, password


def sudo_cmd(client, password, cmd):
    safe = cmd.replace("'", "'\\''")
    full = f"echo '{password}' | sudo -S -p '' sh -c '{safe}'"
    _, stdout, stderr = client.exec_command(full)
    stdout.channel.recv_exit_status()
    return stdout.read().decode().strip()


def parse_ini_conf(text):
    """Parse Synology INI-style config into list of dicts."""
    sections = []
    current = {}
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("["):
            if current:
                sections.append(current)
            current = {"_section": line}
        elif "=" in line:
            # Handle cases where enabled= is appended to iqn= line
            parts = line.split()
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    current[k] = v
    if current:
        sections.append(current)
    return sections


def main():
    # Get K8s PVs
    pv_out = subprocess.check_output(["kubectl", "get", "pv", "-o", "json"]).decode()
    pvs = json.loads(pv_out)
    k8s_pvs = {}
    for pv in pvs["items"]:
        name = pv["metadata"]["name"]
        phase = pv["status"].get("phase", "Unknown")
        ns = pv["spec"].get("claimRef", {}).get("namespace", "")
        claim = pv["spec"].get("claimRef", {}).get("name", "")
        k8s_pvs[name] = {"phase": phase, "namespace": ns, "claim": claim}

    # Get NAS config
    client, password = get_ssh_client()
    lun_conf = sudo_cmd(client, password, "cat /usr/syno/etc/iscsi_lun.conf")
    target_conf = sudo_cmd(client, password, "cat /usr/syno/etc/iscsi_target.conf")
    client.close()

    luns = parse_ini_conf(lun_conf)
    targets = parse_ini_conf(target_conf)

    # Categorize
    bound = []
    released = []
    orphaned = []

    for lun in luns:
        name = lun.get("name", "")
        uuid = lun.get("uuid", "")
        size_bytes = int(lun.get("allocated_size", "0"))
        size_gib = size_bytes / (1024 ** 3)

        # LUN names are "k8s-csi-pvc-UUID", PV names are "pvc-UUID"
        pv_name = name.removeprefix("k8s-csi-")

        if pv_name in k8s_pvs:
            info = k8s_pvs[pv_name]
            if info["phase"] == "Bound":
                bound.append((name, pv_name, uuid, size_gib, info["namespace"], info["claim"]))
            else:
                released.append(
                    (name, pv_name, uuid, size_gib, info["phase"], info["namespace"], info["claim"])
                )
        else:
            orphaned.append((name, uuid, size_gib))

    # Report
    print("=== LUN AUDIT ===")
    print(f"Total LUNs on NAS: {len(luns)}")
    print(f"Total Targets on NAS: {len(targets)}")
    print(f"  Matched to Bound PV: {len(bound)}")
    print(f"  Matched to Released/Available PV: {len(released)}")
    print(f"  No K8s PV (orphaned): {len(orphaned)}")
    print()

    total_orphan_gib = sum(o[2] for o in orphaned)
    total_released_gib = sum(r[3] for r in released)
    total_bound_gib = sum(b[3] for b in bound)
    print(f"Bound LUN storage:    {total_bound_gib:.1f} GiB")
    print(f"Released LUN storage: {total_released_gib:.1f} GiB")
    print(f"Orphaned LUN storage: {total_orphan_gib:.1f} GiB")
    print()

    if released:
        print("--- Released/Available PVs ---")
        for name, pv_name, uuid, size, phase, ns, claim in sorted(released, key=lambda x: x[5]):
            print(f"  {pv_name}  {phase:10s}  {size:6.1f}GiB  {ns}/{claim}")
        print()

    if orphaned:
        print(f"--- Orphaned LUNs ({len(orphaned)} total, safe to delete) ---")
        for name, uuid, size in sorted(orphaned):
            print(f"  {name}  {size:6.1f}GiB  uuid={uuid}")
        print()

    # Summary of targets with no matching LUN
    target_names = {t.get("name", "") for t in targets}
    lun_names = {l.get("name", "") for l in luns}
    orphan_targets = target_names - lun_names
    if orphan_targets:
        print(f"--- Targets with no LUN ({len(orphan_targets)}) ---")
        for name in sorted(orphan_targets):
            print(f"  {name}")


if __name__ == "__main__":
    main()
