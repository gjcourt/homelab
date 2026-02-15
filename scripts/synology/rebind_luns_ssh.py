import paramiko
import os
import sys
import configparser
import io
import time
import argparse

# Configuration
SYNOLOGY_IP = os.environ.get("SYNOLOGY_IP", "192.168.5.8")
SYNOLOGY_USER = os.environ.get("SYNOLOGY_USER")
SYNOLOGY_PASSWORD = os.environ.get("SYNOLOGY_PASSWORD")

# Config Paths
CONFIG_LUN = "/usr/syno/etc/iscsi_lun.conf"
CONFIG_TARGET = "/usr/syno/etc/iscsi_target.conf"
CONFIG_MAPPING = "/usr/syno/etc/iscsi_mapping.conf"
CLI_TOOL = "/usr/local/bin/synoiscsiwebapi"

def get_ssh_client():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        print(f"Connecting to {SYNOLOGY_IP}...", file=sys.stderr)
        client.connect(SYNOLOGY_IP, username=SYNOLOGY_USER, password=SYNOLOGY_PASSWORD, timeout=10)
        return client
    except Exception as e:
        print(f"Failed to connect: {e}", file=sys.stderr)
        sys.exit(1)

def run_command(client, command, use_sudo=True):
    # If user is root, no sudo needed
    if SYNOLOGY_USER == 'root':
        use_sudo = False

    full_cmd = command
    if use_sudo:
        # Simplistic wrapping: echo PASS | sudo -S -p '' sh -c 'CMD'
        # Escape single quotes in command
        cmd_safe = command.replace("'", "'\\''")
        full_cmd = f"echo '{SYNOLOGY_PASSWORD}' | sudo -S -p '' sh -c '{cmd_safe}'"

    # print(f"DEBUG_EXEC: {full_cmd}")

    stdin, stdout, stderr = client.exec_command(full_cmd)

    # Wait for completion
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace').strip()
    err = stderr.read().decode('utf-8', errors='replace').strip()

    return exit_status, out, err

def read_remote_file(client, path):
    print(f"Reading {path}...", file=sys.stderr)
    cmd = f"cat {path}"
    # Read usually works without sudo if files are readable,
    # but strictly speaking /usr/syno/etc might vary.
    # Let's try without sudo first, if fail, try with.
    status, out, err = run_command(client, cmd, use_sudo=False)
    if status != 0:
        # Try with sudo
        status, out, err = run_command(client, cmd, use_sudo=True)
        if status != 0:
             print(f"Error reading {path}: {status} {err}", file=sys.stderr)
             return ""
    return out

def parse_ini(content):
    parser = configparser.ConfigParser(strict=False)
    if not content:
        return parser
    try:
        parser.read_file(io.StringIO(content))
    except Exception as e:
        print(f"Error parsing INI: {e}", file=sys.stderr)
    return parser

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Visualize changes without applying them")
    args = parser.parse_args()

    if not SYNOLOGY_USER or not SYNOLOGY_PASSWORD:
        print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD env vars are required.", file=sys.stderr)
        sys.exit(1)

    client = get_ssh_client()

    try:
        # Read Configs
        lun_conf = read_remote_file(client, CONFIG_LUN)
        target_conf = read_remote_file(client, CONFIG_TARGET)
        mapping_conf = read_remote_file(client, CONFIG_MAPPING)

        luns = parse_ini(lun_conf)
        targets_ini = parse_ini(target_conf)
        mappings = parse_ini(mapping_conf)

        # 1. Map existing targets by Name
        target_map = {}
        for section in targets_ini.sections():
            t_name = targets_ini.get(section, 'name', fallback='')
            t_tid = targets_ini.get(section, 'tid', fallback='')
            t_iqn = targets_ini.get(section, 'iqn', fallback='')
            if t_name:
                target_map[t_name] = {'tid': t_tid, 'iqn': t_iqn, 'section': section}

        # 2. Map existing Mappings
        mapped_lun_ids = set()
        for section in mappings.sections():
            if mappings.has_option(section, 'lun_id'):
                mapped_lun_ids.add(mappings.get(section, 'lun_id'))

        # 3. Find Orphans
        orphans = []
        for section in luns.sections():
            lid = section
            if luns.has_option(section, 'lun_id'):
                lid = luns.get(section, 'lun_id')

            if lid not in mapped_lun_ids:
                name = luns.get(section, 'name', fallback='unknown')
                uuid = luns.get(section, 'uuid', fallback='')
                orphans.append({'id': lid, 'name': name, 'uuid': uuid})

        print(f"Found {len(orphans)} orphaned LUNs / {len(target_map)} Targets.", file=sys.stderr)

        if not orphans:
            print("No repairs needed.")
            return

        print("Planning repairs:", file=sys.stderr)

        # 4. Repair
        success_count = 0
        fail_count = 0

        for orphan in orphans:
            name = orphan['name']
            uuid = orphan['uuid']

            if name == 'unknown':
                 continue

            matching_target = target_map.get(name)

            if matching_target:
                tid = matching_target['tid']

                if args.dry_run:
                    print(f"[DRY-RUN] Would map LUN {name} ({uuid}) -> Target {tid}")
                else:
                    # NOTE: Older Synology CLI syntax might differ.
                    # If `target map_lun` fails, we might need a different command structure.
                    # But based on `synoiscsiwebapi --help` from probe (not seen but inferred), this is standard.
                    cmd_map = f"{CLI_TOOL} target map_lun {tid} {uuid}"
                    print(f"[REMAP] Mapping {name} -> Target {tid}")
                    s, out, err = run_command(client, cmd_map, use_sudo=True)
                    if s == 0:
                        print("  SUCCESS: Mapped.")
                        success_count += 1
                    else:
                        print(f"  FAILED: {err}")
                        fail_count += 1
            else:
                print(f"[SKIP] {name}: No matching target found.")

        if not args.dry_run:
             print(f"\nSummary: {success_count} Succeeded, {fail_count} Failed.")

    finally:
        client.close()

if __name__ == "__main__":
    main()
