#!/usr/bin/env python3
"""Enable all disabled iSCSI targets on the Synology NAS.

This script connects via SSH and uses synoiscsiwebapi to enable
all targets that currently have enabled=no in the config.

Environment variables:
    SYNOLOGY_USER     - SSH username (default: manager)
    SYNOLOGY_HOST     - NAS IP (default: 192.168.5.8)
    SYNOLOGY_PASSWORD - SSH password (required)
"""

import os
import sys
import paramiko


def main():
    ip = os.environ.get("SYNOLOGY_HOST", "192.168.5.8")
    user = os.environ.get("SYNOLOGY_USER", "manager")
    password = os.environ.get("SYNOLOGY_PASSWORD")
    if not password:
        print("ERROR: SYNOLOGY_PASSWORD not set")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username=user, password=password)

    def sudo_cmd(cmd):
        safe = cmd.replace("'", "'\\''" )
        full = f"echo '{password}' | sudo -S -p '' sh -c '{safe}'"
        stdin, stdout, stderr = client.exec_command(full)
        status = stdout.channel.recv_exit_status()
        return status, stdout.read().decode().strip(), stderr.read().decode().strip()

    # Parse the config to find disabled target IDs
    status, conf, _ = sudo_cmd("cat /usr/syno/etc/iscsi_target.conf")
    if status != 0:
        print("ERROR: Failed to read target config.")
        client.close()
        sys.exit(1)

    # Parse INI-style config to find disabled targets
    tids = []
    current_tid = None
    current_enabled = None
    for line in conf.split("\n"):
        line = line.strip()
        if line.startswith("[iSCSI_T"):
            # Save previous section if disabled
            if current_tid is not None and current_enabled == "no":
                tids.append(current_tid)
            current_tid = None
            current_enabled = None
        if line.startswith("tid="):
            current_tid = line.split("=", 1)[1].strip()
        # enabled= might be on its own line or appended to another line
        if "enabled=no" in line:
            current_enabled = "no"
        elif "enabled=yes" in line:
            current_enabled = "yes"
    # Don't forget the last section
    if current_tid is not None and current_enabled == "no":
        tids.append(current_tid)

    if not tids:
        print("No disabled targets found.")
        client.close()
        return

    print(f"Found {len(tids)} disabled targets to enable.")

    success = 0
    fail = 0
    for tid in tids:
        s, out, err = sudo_cmd(
            f"/usr/local/bin/synoiscsiwebapi target enable {tid}"
        )
        if s == 0:
            success += 1
        else:
            print(f"  FAILED tid={tid}: exit={s} err={err}")
            fail += 1

    print(f"\nDone: {success} enabled, {fail} failed.")

    # Verify
    s, out, _ = sudo_cmd(
        "grep '^enabled=' /usr/syno/etc/iscsi_target.conf | sort | uniq -c"
    )
    print(f"Current state:\n{out}")
    client.close()


if __name__ == "__main__":
    main()
