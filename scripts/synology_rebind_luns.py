import os
import requests
import json
import urllib3
import argparse

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SYNOLOGY_IP = "192.168.5.8"
SYNOLOGY_PORT = "5001"
SYNOLOGY_USER = os.getenv("SYNOLOGY_USER")
SYNOLOGY_PASS = os.getenv("SYNOLOGY_PASSWORD")

if not SYNOLOGY_USER or not SYNOLOGY_PASS:
    print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD environment variables must be set.")
    exit(1)

BASE_URL = f"https://{SYNOLOGY_IP}:{SYNOLOGY_PORT}/webapi"

def login(session):
    auth_url = f"{BASE_URL}/auth.cgi"
    params = {
        "api": "SYNO.API.Auth",
        "version": "3",
        "method": "login",
        "account": SYNOLOGY_USER,
        "passwd": SYNOLOGY_PASS,
        "session": "Core",
        "format": "cookie"
    }
    r = session.get(auth_url, params=params, verify=False)
    try:
        data = r.json()
        if data.get("success"):
            return True, data.get("data", {}).get("sid")
    except:
        pass
    return False, None

def list_targets(session):
    url = f"{BASE_URL}/entry.cgi"
    params = {"api": "SYNO.Core.ISCSI.Target", "version": "1", "method": "list"}
    r = session.post(url, data=params, verify=False)
    data = r.json()
    return data.get("data", {}).get("targets", [])

def list_luns(session):
    url = f"{BASE_URL}/entry.cgi"
    params = {"api": "SYNO.Core.ISCSI.LUN", "version": "1", "method": "list"}
    r = session.post(url, data=params, verify=False)
    data = r.json()
    return data.get("data", {}).get("luns", [])

def delete_target(session, target_id):
    url = f"{BASE_URL}/entry.cgi"
    params = {
        "api": "SYNO.Core.ISCSI.Target",
        "version": "1",
        "method": "delete",
        "target_id_list": f"[{target_id}]"
    }
    r = session.post(url, data=params, verify=False)
    return r.json()

def create_target(session, name, iqn, lun_id):
    url = f"{BASE_URL}/entry.cgi"
    params = {
        "api": "SYNO.Core.ISCSI.Target",
        "version": "1",
        "method": "create",
        "name": name,
        "iqn": iqn,
        "auth_type": 0,
        "max_sessions": 1,
        "lun_id_list": f"[{lun_id}]"
    }
    r = session.post(url, data=params, verify=False)
    return r.json()

def map_lun_to_target(session, target_id, lun_id):
    url = f"{BASE_URL}/entry.cgi"
    params = {
        "api": "SYNO.Core.ISCSI.Target",
        "version": "1",
        "method": "set",
        "target_id": target_id,
        "lun_id_list": f"[{lun_id}]"
    }
    r = session.post(url, data=params, verify=False)
    return r.json()

def main():
    parser = argparse.ArgumentParser(description="Fix Synology iSCSI Mappings")
    parser.add_argument("--dry-run", action="store_true", help="Print actions without executing")
    args = parser.parse_args()

    session = requests.Session()
    success, sid = login(session)
    if not success:
        print("Login failed")
        exit(1)

    print("Fetching Targets and LUNs...")
    targets = list_targets(session)
    luns = list_luns(session)

    # Map LUNs by name for easy lookup
    lun_map = {l['name']: l['lun_id'] for l in luns}

    mapped_count = 0
    recreated_count = 0
    cleaned_count = 0

    for t in targets:
        target_name = t['name']
        target_id = t['target_id']
        mapping_index = t.get('mapping_index', -1)

        # Cleanup "recovery-target"
        if "recovery-target" in target_name:
            print(f"Cleanup: Deleting {target_name}...")
            if not args.dry_run:
                resp = delete_target(session, target_id)
                if resp.get('success'):
                    print("  > Deleted.")
                    cleaned_count += 1
                else:
                     print(f"  > Delete Failed: {resp}")
            continue

        if mapping_index != -1:
            continue

        if target_name in lun_map:
            lun_id = lun_map[target_name]
            iqn = t.get('iqn')

            print(f"Found orphaned Target '{target_name}' (ID: {target_id}) -> LUN: {lun_id}")

            if not args.dry_run:
                # Primary Strategy: Just Map (Talos is offline, so locks should be gone)
                print("  > Attempting to MAP LUN to Target...")
                map_resp = map_lun_to_target(session, target_id, lun_id)
                if map_resp.get('success'):
                    print("    > Map Success!")
                    mapped_count += 1
                else:
                    print(f"    > Map Failed: {map_resp}. Attempting DELETE & RECREATE...")

                    # Secondary Strategy: Delete & Recreate
                    del_resp = delete_target(session, target_id)
                    if not del_resp.get('success'):
                         print(f"    > Delete Failed: {del_resp}. Cannot fix this target.")
                    else:
                        print(f"    > Creating replacement target with IQN: {iqn}...")
                        create_resp = create_target(session, target_name, iqn, lun_id)
                        if create_resp.get('success'):
                            print("      > Recreate Success!")
                            recreated_count += 1
                        else:
                            print(f"      > Recreate Failed! CRITICAL: Target deleted but not recreated! Error: {create_resp}")

            else:
                print("  > [Dry Run] Would MAP (and fallback to DELETE + CREATE)")

    print(f"\nProcessing complete. Recreated {recreated_count} targets. Mapped {mapped_count} targets. Cleaned {cleaned_count} temporary targets.")

if __name__ == "__main__":
    main()
