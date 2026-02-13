import paramiko
import os
import time
import base64

"""
Script: synology_prune_orphans.py
Description:
    Identifies and removes "orphaned" iSCSI Targets and LUNs on Synology NAS.
    An orphan is defined as:
    - A Target that is not referenced in iscsi_mapping.conf
    - A LUN that is not referenced in iscsi_mapping.conf

    This script is useful when the Synology API refuses to delete objects (returns error 18990710 "Target Busy")
    or when the target limit (128) is reached due to stale objects.

Usage:
    export SYNOLOGY_USER="admin_user"
    export SYNOLOGY_PASSWORD="password"
    export SYNOLOGY_IP="192.168.x.x"
    python3 synology_prune_orphans.py

Process:
    1. Connects via SSH and reads target, lun, and mapping configs.
    2. Identifies orphans by comparing IDs against the mapping file.
    3. Stops the 'pkg-iscsi' service.
    4. Rewrites the config files excluding the orphaned blocks.
    5. Restarts the service.
"""

SYNOLOGY_IP = os.environ.get("SYNOLOGY_IP", "192.168.5.8")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD")

TARGET_CONF_PATH = "/usr/syno/etc/iscsi_target.conf"
LUN_CONF_PATH = "/usr/syno/etc/iscsi_lun.conf"
MAPPING_CONF_PATH = "/usr/syno/etc/iscsi_mapping.conf"

def run_ssh_command(client, command, sudo=False):
    if sudo:
        command = f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' {command}"
    stdin, stdout, stderr = client.exec_command(command)
    return stdout.read().decode('utf-8')

def parse_conf_identifiers(content, block_prefix):
    """
    Returns a dict of identifiers (tid for Targets, uuid for LUNs) -> Block Header Line
    """
    identifiers = {}
    current_block_header = None

    for line in content.splitlines():
        line = line.strip()
        if line.startswith(f"[{block_prefix}"):
            current_block_header = line
        elif "=" in line and current_block_header:
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip()
            if block_prefix == "iSCSI_T" and k == "tid":
                identifiers[v] = current_block_header
            elif block_prefix == "LUN_" and k == "uuid":
                identifiers[v] = current_block_header
    return identifiers

def get_orphans(client):
    print("  Fetching configs...")
    target_conf = run_ssh_command(client, f"cat {TARGET_CONF_PATH}", sudo=True)
    lun_conf = run_ssh_command(client, f"cat {LUN_CONF_PATH}", sudo=True)
    map_conf = run_ssh_command(client, f"cat {MAPPING_CONF_PATH}", sudo=True)

    # Parse Mappings
    mapped_tids = set()
    mapped_uuids = set()
    for line in map_conf.splitlines():
        line = line.strip()
        if "tid=" in line:
            mapped_tids.add(line.split("=")[1].strip())
        if "uuid=" in line:
            mapped_uuids.add(line.split("=")[1].strip())

    # Identify Orphans
    orphan_tids = []
    orphan_uuids = []

    # Parse Targets
    current_tid = None
    for line in target_conf.splitlines():
        if "tid=" in line:
            current_tid = line.split("=")[1].strip()
            if current_tid not in mapped_tids:
                orphan_tids.append(current_tid)

    # Parse LUNs
    current_uuid = None
    for line in lun_conf.splitlines():
        if "uuid=" in line:
            current_uuid = line.split("=")[1].strip()
            if current_uuid not in mapped_uuids:
                orphan_uuids.append(current_uuid)

    return list(set(orphan_tids)), list(set(orphan_uuids))

def clean_conf_file(client, remote_path, block_prefix, identifiers_to_remove, id_key):
    print(f"  Cleaning {remote_path}...")
    content = run_ssh_command(client, f"cat {remote_path}", sudo=True)
    lines = content.splitlines(keepends=True)

    new_lines = []
    current_block = []
    remove_block = False

    removed_count = 0

    for line in lines:
        stripped = line.strip()
        # Check start of block
        if stripped.startswith(f"[{block_prefix}"):
            # Process previous block
            if current_block:
                if remove_block:
                    removed_count += 1
                else:
                    new_lines.extend(current_block)

            # Reset
            current_block = [line]
            remove_block = False

            # Check if this header matches (for LUNs, header has UUID sometimes? [LUN_UUID])
            # For Targets: [iSCSI_T12]
            # For LUNs: [LUN_UUID]

            if block_prefix == "LUN_":
                # Header acts as ID check if it contains UUID
                for uuid in identifiers_to_remove:
                    if uuid in stripped:
                        remove_block = True
                        break
        else:
            current_block.append(line)
            # Check ID within block
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                if k == id_key and v in identifiers_to_remove:
                    remove_block = True

    # Flush last
    if current_block:
        if remove_block:
            removed_count += 1
        else:
            new_lines.extend(current_block)

    print(f"    Removed {removed_count} blocks.")

    if removed_count > 0:
        return new_lines
    return None

def upload_and_swap(client, new_lines, remote_path):
    import base64
    cleaned_content = "".join(new_lines)
    b64_content = base64.b64encode(cleaned_content.encode('utf-8')).decode('utf-8')

    temp_b64 = f"/tmp/{os.path.basename(remote_path)}.b64"
    temp_remote = f"/tmp/{os.path.basename(remote_path)}.cleaned"

    print(f"    Uploading cleaned config ({len(b64_content)} bytes)...")

    # Upload in chunks
    chunk_size = 1000
    client.exec_command(f"rm {temp_b64} {temp_remote}")

    for i in range(0, len(b64_content), chunk_size):
        chunk = b64_content[i:i+chunk_size]
        cmd = f"echo '{chunk}' >> {temp_b64}"
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            raise Exception(f"Upload failed: {stderr.read().decode()}")

    stdin, stdout, stderr = client.exec_command(f"base64 -d {temp_b64} > {temp_remote}")
    if stdout.channel.recv_exit_status() != 0:
         raise Exception("Base64 decode failed")

    # Swap
    print(f"    Swapping {remote_path}...")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' cp {temp_remote} {remote_path}")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' chmod 644 {remote_path}")

def main():
    if not SYNOLOGY_USER: return

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(SYNOLOGY_IP, username=SYNOLOGY_USER, password=SYNOLOGY_PASSWORD)

    print("Phase 1: Identify Orphans")
    orphan_tids, orphan_uuids = get_orphans(client)
    print(f"  Orphan TIDs: {len(orphan_tids)}")
    print(f"  Orphan UUIDs: {len(orphan_uuids)}")

    if not orphan_tids and not orphan_uuids:
        print("Clean.")
        client.close()
        return

    print("Phase 2: Stop Service")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' systemctl stop pkg-iscsi")
    time.sleep(5)

    if orphan_tids:
        new_target_lines = clean_conf_file(client, TARGET_CONF_PATH, "iSCSI_T", orphan_tids, "tid")
        if new_target_lines:
            upload_and_swap(client, new_target_lines, TARGET_CONF_PATH)

    if orphan_uuids:
        new_lun_lines = clean_conf_file(client, LUN_CONF_PATH, "LUN_", orphan_uuids, "uuid")
        if new_lun_lines:
            upload_and_swap(client, new_lun_lines, LUN_CONF_PATH)

    print("Phase 3: Start Service")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' systemctl start pkg-iscsi")
    print("Done.")
    client.close()

if __name__ == "__main__":
    main()
