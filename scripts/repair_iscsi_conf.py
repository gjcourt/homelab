import paramiko
import os
import sys

"""
Script: repair_iscsi_conf.py
Description:
    Surgically removes entries containing "recovery" strings from the Synology iscsi_target.conf file.
    It stops the iSCSI service, downloads the config, filters out the bad blocks, uploads the clean version via Base64 to avoid shell limits, and restarts the service.

Usage:
    export SYNOLOGY_USER="admin_user"
    export SYNOLOGY_PASSWORD="password"
    export SYNOLOGY_IP="192.168.x.x"
    python3 repair_iscsi_conf.py

Safety:
    - Backs up the original config to /usr/syno/etc/iscsi_target.conf.bak_recovery_fix
    - Uses 'sudo' for file operations.
    - Requires 'manager' or root-level permissions on the NAS.
"""

SYNOLOGY_IP = os.environ.get("SYNOLOGY_IP", "192.168.5.8")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD")

REMOTE_PATH = "/usr/syno/etc/iscsi_target.conf"
BACKUP_PATH = "/usr/syno/etc/iscsi_target.conf.bak_recovery_fix"

def main():
    if not SYNOLOGY_USER or not SYNOLOGY_PASSWORD:
        print("Missing credentials")
        return

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    print(f"Connecting to {SYNOLOGY_IP}...")
    try:
        client.connect(SYNOLOGY_IP, username=SYNOLOGY_USER, password=SYNOLOGY_PASSWORD)
    except Exception as e:
        print(f"Connection failed: {e}")
        return

    # sftp = client.open_sftp()

    # 1. Stop Service to prevent overwrite on exit
    print("Stopping pkg-iscsi service...")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' systemctl stop pkg-iscsi")
    # Give it a moment to stop and flush
    import time
    time.sleep(5)

    # 2. Download (after stop, to get the flushed state)
    print(f"Reading {REMOTE_PATH}...")
    try:
        # Read via sudo cat
        cmd = f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' cat {REMOTE_PATH}"
        stdin, stdout, stderr = client.exec_command(cmd)
        content = stdout.read().decode('utf-8')
        lines = content.splitlines(keepends=True)
        # Check if we got content
        if not lines:
             err = stderr.read().decode('utf-8')
             print(f"Read error or empty file: {err}")
             # Try starting service back up if we fail here?
             client.close()
             return
    except Exception as e:
        print(f"Failed to read remote file: {e}")
        client.close()
        return

    # 3. Process
    print(f"Processing {len(lines)} lines...")
    new_lines = []
    current_block = []
    has_recovery = False

    removed_count = 0
    kept_count = 0

    for line in lines:
        stripped = line.strip()
        # Blocks seem to start with [iSCSI_T...]
        if stripped.startswith('[iSCSI_T') or stripped.startswith('[iSCSI_LUN'):
             if current_block:
                 if has_recovery:
                     removed_count += 1
                 else:
                     new_lines.extend(current_block)
                     kept_count += 1

             # Reset for new block
             current_block = [line]
             has_recovery = False
        else:
            current_block.append(line)
            if "recovery" in line:
                has_recovery = True

    # Flush last block
    if current_block:
        if has_recovery:
            removed_count += 1
        else:
            new_lines.extend(current_block)
            kept_count += 1

    print(f"Analysis complete. Removed {removed_count} blocks. Kept {kept_count} blocks.")

    if removed_count == 0:
        print("No recovery blocks found to remove. Exiting.")
        client.close()
        return

    # 3. Upload to /tmp
    # 3. Upload to /tmp via Base64
    import base64

    cleaned_content = "".join(new_lines)
    b64_content = base64.b64encode(cleaned_content.encode('utf-8')).decode('utf-8')

    temp_b64 = "/tmp/iscsi_target.b64"
    temp_remote = "/tmp/iscsi_target.conf_cleaned"

    print(f"Uploading base64 encoded config ({len(b64_content)} bytes)...")

    # Clean up old temp
    client.exec_command(f"rm {temp_b64} {temp_remote}")

    # Upload in chunks to avoid command line length limits
    chunk_size = 1000
    for i in range(0, len(b64_content), chunk_size):
        chunk = b64_content[i:i+chunk_size]
        cmd = f"echo '{chunk}' >> {temp_b64}"
        stdin, stdout, stderr = client.exec_command(cmd)
        exit_status = stdout.channel.recv_exit_status()
        if exit_status != 0:
            print(f"Chunk upload failed at index {i}: {stderr.read().decode()}")
            return

    print("Decoding base64 on remote...")
    stdin, stdout, stderr = client.exec_command(f"base64 -d {temp_b64} > {temp_remote}")
    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        print(f"Decode failed: {stderr.read().decode()}")
        return

    # 4. Swap and Restart
    stdin, stdout, stderr = client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' cp {REMOTE_PATH} {BACKUP_PATH}")
    print(stdout.read().decode())
    print(stderr.read().decode())

    print("Overwriting original file...")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' cp {temp_remote} {REMOTE_PATH}")
    client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' chmod 644 {REMOTE_PATH}")

    print("Starting pkg-iscsi service...")
    stdin, stdout, stderr = client.exec_command(f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' systemctl start pkg-iscsi")
    out = stdout.read().decode()
    err = stderr.read().decode()
    print(out)
    if err:
        print(f"STDERR: {err}")

    print("Done!")
    client.close()


if __name__ == "__main__":
    main()
