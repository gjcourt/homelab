#!/usr/bin/env python3
"""Delete orphaned iSCSI LUNs from the Synology NAS.

Removes LUNs (and their associated targets/mappings) that have no
corresponding Kubernetes PersistentVolume. Runs in dry-run mode by
default; pass --execute to actually delete.

Environment variables:
    SYNOLOGY_USER     - SSH username (default: manager)
    SYNOLOGY_HOST     - NAS IP (default: 192.168.5.8)
    SYNOLOGY_PASSWORD - SSH password (required)

Usage:
    python3 cleanup_orphans.py          # dry-run: shows what would be deleted
    python3 cleanup_orphans.py --execute  # actually deletes orphaned LUNs
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
    status = stdout.channel.recv_exit_status()
    return status, stdout.read().decode().strip(), stderr.read().decode().strip()


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
            parts = line.split()
            for part in parts:
                if "=" in part:
                    k, v = part.split("=", 1)
                    current[k] = v
    if current:
        sections.append(current)
    return sections


def main():
    execute = "--execute" in sys.argv

    # Get K8s PV names
    pv_out = subprocess.check_output(["kubectl", "get", "pv", "-o", "json"]).decode()
    pvs = json.loads(pv_out)
    k8s_pv_names = {pv["metadata"]["name"] for pv in pvs["items"]}

    # Get NAS config
    client, password = get_ssh_client()
    _, lun_conf, _ = sudo_cmd(client, password, "cat /usr/syno/etc/iscsi_lun.conf")
    _, target_conf, _ = sudo_cmd(client, password, "cat /usr/syno/etc/iscsi_target.conf")
    _, mapping_conf, _ = sudo_cmd(client, password, "cat /usr/syno/etc/iscsi_mapping.conf")

    luns = parse_ini_conf(lun_conf)
    targets = parse_ini_conf(target_conf)
    mappings = parse_ini_conf(mapping_conf)

    # Build lookup: target name -> tid
    target_by_name = {}
    for t in targets:
        name = t.get("name", "")
        tid = t.get("tid", "")
        if name and tid:
            target_by_name[name] = tid

    # Find orphaned LUNs (LUN name stripped of "k8s-csi-" prefix doesn't match any PV)
    orphaned = []
    for lun in luns:
        name = lun.get("name", "")
        uuid = lun.get("uuid", "")
        pv_name = name.removeprefix("k8s-csi-")
        if pv_name not in k8s_pv_names:
            orphaned.append((name, uuid))

    if not orphaned:
        print("No orphaned LUNs found.")
        client.close()
        return

    mode = "EXECUTING" if execute else "DRY-RUN"
    print(f"=== {mode}: {len(orphaned)} orphaned LUNs to remove ===\n")

    success = 0
    fail = 0
    for name, uuid in sorted(orphaned):
        tid = target_by_name.get(name, "")
        if not execute:
            print(f"  Would delete LUN {name} (uuid={uuid}) + target tid={tid}")
            continue

        # Step 1: Unmap LUN from target
        if tid:
            s, out, err = sudo_cmd(
                client, password,
                f"/usr/local/bin/synoiscsiwebapi target unmap_lun {tid} {uuid}"
            )
            if s != 0:
                print(f"  WARN: unmap failed for tid={tid} uuid={uuid}: {err}")

        # Step 2: Delete the LUN
        s, out, err = sudo_cmd(
            client, password,
            f"/usr/local/bin/synoiscsiwebapi lun delete {uuid}"
        )
        if s != 0:
            print(f"  FAIL: lun delete {name}: {err}")
            fail += 1
            continue

        # Step 3: Delete the target (if it exists)
        if tid:
            s, out, err = sudo_cmd(
                client, password,
                f"/usr/local/bin/synoiscsiwebapi target delete {tid}"
            )
            if s != 0:
                print(f"  WARN: target delete tid={tid}: {err}")

        success += 1
        print(f"  Deleted LUN {name} + target tid={tid}")

    print(f"\n{mode} complete: {success} deleted, {fail} failed.")

    # Re-check counts
    if execute:
        _, out, _ = sudo_cmd(
            client, password,
            "grep -c '\\[iSCSI_LUN' /usr/syno/etc/iscsi_lun.conf"
        )
        print(f"Remaining LUNs: {out}")
        _, out, _ = sudo_cmd(
            client, password,
            "grep -c '\\[iSCSI_T' /usr/syno/etc/iscsi_target.conf"
        )
        print(f"Remaining Targets: {out}")

    client.close()


if __name__ == "__main__":
    main()
