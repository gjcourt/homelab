import paramiko
import os
import sys
import time
import argparse
import base64

"""
Script: synology_prune_zombies.py
Description:
    Identifies and removes "Zombie" iSCSI Targets on Synology NAS via SSH (direct config editing).
    A zombie target is defined as:
    - An iSCSI target present on the NAS
    - Whose IQN contains a PVC UUID that is NOT in the list of currently active Kubernetes PVCs.

    This script is necessary because the Synology Web API often fails to delete these targets
    with error 18990710 ("Target Busy") even when sessions are 0.

    The script removes the Target from iscsi_target.conf and also removes any associated
    mappings from iscsi_mapping.conf.

Usage:
    export SYNOLOGY_USER="admin_user"
    export SYNOLOGY_PASSWORD="password"
    export SYNOLOGY_IP="192.168.5.8"
    kubectl get pv -o json | jq -r '...' > active_pvs.txt
    python3 scripts/synology_prune_zombies.py active_pvs.txt [--dry-run]

SYNOLOGY_IP = os.environ.get("SYNOLOGY_IP", "192.168.5.8")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD")

TARGET_CONF_PATH = "/usr/syno/etc/iscsi_target.conf"
MAPPING_CONF_PATH = "/usr/syno/etc/iscsi_mapping.conf"

def run_ssh_command(client, command, sudo=False):
    if sudo:
        # Escape single quotes in the command for the sh -c wrapper
        cmd_escaped = command.replace("'", "'\\''")
        command = f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' sh -c '{cmd_escaped}'"

    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    if exit_status != 0:
        raise Exception(f"Command failed ({exit_status}): {command}\nStderr: {err}")
    return out

def read_remote_file(client, path):
    return run_ssh_command(client, f"cat {path}", sudo=True)

def restart_iscsi_service(client):
    print("Restarting iSCSI service...")

    # Use systemctl for modern DSM
    try:
        run_ssh_command(client, "systemctl stop pkg-iscsi", sudo=True)
        time.sleep(2)
        run_ssh_command(client, "systemctl start pkg-iscsi", sudo=True)
        print("iSCSI service restarted.")
    except Exception as e:
        print(f"Failed to restart service via systemctl: {e}")
        # Fallback to synoservice just in case (older DSM)
        try:
            print("Attempting fallback to synoservice...")
            run_ssh_command(client, "/usr/syno/sbin/synoservice --restart pkg-iscsi", sudo=True)
        except Exception as e2:
             print(f"Fallback failed too: {e2}")
             raise

def write_remote_file(client, path, content):
    # Use base64 to avoid shell escaping issues
    b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    # Use tee to handle sudo write permissions correctly
    cmd = f"echo '{b64_content}' | base64 -d | tee {path} > /dev/null"
    run_ssh_command(client, cmd, sudo=True)

def parse_target_conf(content):
    targets = [] # List of dicts
    current_target = {}

    for line in content.splitlines():
        line = line.strip()
        # Synology uses [iSCSI_Target] or [iSCSI_TX]
        if line.startswith("[iSCSI_T"):
            if current_target:
                targets.append(current_target)
            current_target = {"_header": line, "_lines": [line]}
        elif current_target:
            current_target["_lines"].append(line)
            if "=" in line:
                k, v = line.split("=", 1)
                current_target[k.strip()] = v.strip()

    if current_target:
        targets.append(current_target)
    return targets

def parse_mapping_conf(content):
    mappings = [] # List of dicts
    current_mapping = {}

    for line in content.splitlines():
        line = line.strip()
        # Synology uses [iSCSI_Mapping] or [iSCSI_MAP_...]
        if line.startswith("[iSCSI_MAP") or line.startswith("[iSCSI_Mapping"):
            if current_mapping:
                mappings.append(current_mapping)
            current_mapping = {"_header": line, "_lines": [line]}
        elif current_mapping:
            current_mapping["_lines"].append(line)
            if "=" in line:
                k, v = line.split("=", 1)
                current_mapping[k.strip()] = v.strip()

    if current_mapping:
        mappings.append(current_mapping)
    return mappings

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pvs_file", help="File containing list of valid PVC volume handles (UUIDs)")
    parser.add_argument("--dry-run", action="store_true", help="Do not delete, only list")
    args = parser.parse_args()

    # 1. Load active PVs
    if not os.path.exists(args.pvs_file):
        print(f"Error: File {args.pvs_file} not found")
        sys.exit(1)

    with open(args.pvs_file, 'r') as f:
        valid_uuids = set(line.strip() for line in f if line.strip())
    print(f"Loaded {len(valid_uuids)} valid PV UUIDs.")

    # 2. Connect SSH
    if not SYNOLOGY_USER or not SYNOLOGY_PASSWORD:
        print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD must be set.")
        sys.exit(1)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"Connecting to {SYNOLOGY_IP}...")
    client.connect(SYNOLOGY_IP, username=SYNOLOGY_USER, password=SYNOLOGY_PASSWORD)

    # 3. Read Configs
    print("Reading remote configs...")
    target_conf_raw = read_remote_file(client, TARGET_CONF_PATH)
    mapping_conf_raw = read_remote_file(client, MAPPING_CONF_PATH)

    targets = parse_target_conf(target_conf_raw)
    mappings = parse_mapping_conf(mapping_conf_raw)

    print(f"Found {len(targets)} targets and {len(mappings)} mappings.")

    # 4. Identify Zombies
    zombie_tids = set()
    targets_to_keep = []

    for t in targets:
        iqn = t.get("iqn", "")
        tid = t.get("tid")

        # Check if CSI target
        if "pvc-" not in iqn:
            targets_to_keep.append(t)
            continue

        # Check if valid
        is_known = False
        for uuid in valid_uuids:
            if uuid in iqn:
                is_known = True
                break

        if not is_known:
            print(f"Found Zombie Target: {iqn} (tid={tid})")
            zombie_tids.add(tid)
        else:
            targets_to_keep.append(t)

    print(f"Identified {len(zombie_tids)} zombie targets to remove.")

    if not zombie_tids:
        print("No zombies found. Exiting.")
        return

    # 5. Filter Mappings
    mappings_to_keep = []
    for m in mappings:
        tid = m.get("tid")
        if tid in zombie_tids:
            print(f"Removing Mapping for Zombie TID {tid}")
        else:
            mappings_to_keep.append(m)

    if args.dry_run:
        print("Dry run complete. No changes made.")
        return

    confirm = input(f"Are you sure you want to write changes to Synology? This will restart the iSCSI service. [y/N]: ")
    if confirm.lower() != 'y':
        print("Aborted.")
        return

    # 6. Reconstruct Configs
    print("Reconstructing config files...")

    new_target_conf = ""
    for t in targets_to_keep:
        new_target_conf += "\n".join(t["_lines"]) + "\n"

    new_mapping_conf = ""
    for m in mappings_to_keep:
        new_mapping_conf += "\n".join(m["_lines"]) + "\n"

    # 7. Apply Changes
    print("Backing up configs...")
    run_ssh_command(client, f"cp {TARGET_CONF_PATH} {TARGET_CONF_PATH}.bak_zombie_prune", sudo=True)
    run_ssh_command(client, f"cp {MAPPING_CONF_PATH} {MAPPING_CONF_PATH}.bak_zombie_prune", sudo=True)

    print("Stopping iSCSI service...")
    try:
        run_ssh_command(client, "systemctl stop pkg-iscsi", sudo=True)
    except Exception as e:
        print(f"Warning stopping service (systemctl failed): {e}")
        try:
             run_ssh_command(client, "/usr/syno/sbin/synoservice --stop pkg-iscsi", sudo=True)
        except Exception as e2:
             print(f"Warning stopping service (synoservice failed): {e2}")

    print("Writing new configs...")
    write_remote_file(client, TARGET_CONF_PATH, new_target_conf)
    write_remote_file(client, MAPPING_CONF_PATH, new_mapping_conf)

    print("Starting iSCSI service...")
    try:
        run_ssh_command(client, "systemctl start pkg-iscsi", sudo=True)
    except Exception as e:
        print(f"Error starting service (systemctl): {e}")
        try:
            run_ssh_command(client, "/usr/syno/sbin/synoservice --start pkg-iscsi", sudo=True)
        except Exception as e2:
             print(f"Error starting service (synoservice): {e2}")
             print("You may need to manually start the service on the NAS.")

    print("Done. Please verify connectivity.")

if __name__ == "__main__":
    main()
