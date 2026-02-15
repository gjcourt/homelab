import os
import requests
import json
import urllib3
import argparse
import sys

"""
Script: scripts/synology/rebind_luns.py
Description:
    Interacts with the Synology Web API to manage iSCSI Targets and LUN mappings.
    Diagnoses missing targets (where a LUN exists but no Target maps to it)
    and recreates them.

Usage:
    export SYNOLOGY_USER="admin_user"
    export SYNOLOGY_PASSWORD="password"
    export SYNOLOGY_HOST="192.168.1.50" (Optional, defaults to 192.168.1.50)
    python3 scripts/synology/rebind_luns.py [--dry-run]
"""

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SYNOLOGY_IP = os.getenv("SYNOLOGY_HOST", "192.168.1.50")
SYNOLOGY_PORT = "5001"
SYNOLOGY_USER = os.getenv("SYNOLOGY_USER")
SYNOLOGY_PASS = os.getenv("SYNOLOGY_PASSWORD")

if not SYNOLOGY_USER or not SYNOLOGY_PASS:
    print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD environment variables must be set.")
    sys.exit(1)

BASE_URL = f"https://{SYNOLOGY_IP}:{SYNOLOGY_PORT}/webapi"

def login(session):
    print(f"Logging in to {BASE_URL}...")
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
    try:
        r = session.get(auth_url, params=params, verify=False, timeout=10)
        data = r.json()
        if data.get("success"):
            return True, data.get("data", {}).get("sid")
        else:
            print(f"Login Response: {data}")
    except Exception as e:
        print(f"Login Connection Error: {e}")
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
        "max_sessions": 0, # 0 = Unlimited
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
        print("Login failed. Check IP, Credentials, and Network.")
        sys.exit(1)

    print("Fetching Targets and LUNs...")
    try:
        targets = list_targets(session)
        luns = list_luns(session)
    except Exception as e:
        print(f"Error listing resources: {e}")
        sys.exit(1)

    print(f"Found {len(targets)} Targets and {len(luns)} LUNs.")

    # Map LUN ID to LUN Object
    lun_by_id = {l['lun_id']: l for l in luns}
    
    # Track which LUNs are mapped
    mapped_lun_ids = set()
    
    # 1. Check Existing Targets
    for t in targets:
        target_name = t['name']
        target_id = t['target_id']
        mapped_luns = t.get('mapped_lun_list', [])
        
        for m in mapped_luns:
            mapped_lun_ids.add(m['lun_id'])

        # cleanup
        if "recovery-target" in target_name:
             if not args.dry_run:
                 resp = delete_target(session, target_id)
                 print(f"Deleted temp target {target_name}: {resp.get('success')}")

    # 2. Find Orphaned LUNs (LUNs with no Target)
    orphaned_luns = [l for l in luns if l['lun_id'] not in mapped_lun_ids]
    
    print(f"Found {len(orphaned_luns)} Orphaned LUNs (No Target attached).")

    for lun in orphaned_luns:
        lun_name = lun['name'] # Usually the PVC name e.g. "pvc-..."
        lun_uuid = lun['uuid']
        lun_id = lun['lun_id']
        
        # We need to create a Target for this LUN.
        # Naming convention: The previous targets often use the IQN format.
        # We'll construct a valid IQN.
        # iqn.2000-01.com.synology:kube. is a robust prefix
        
        target_iqn = f"iqn.2000-01.com.synology:kube.{lun_name}"
        # Target Name (Display Name)
        target_name = f"kube-{lun_name}" 
        
        print(f"Restoring Target for LUN '{lun_name}'...")
        print(f"  > Proposed IQN: {target_iqn}")
        
        if not args.dry_run:
            resp = create_target(session, target_name, target_iqn, lun_id)
            if resp.get('success'):
                print(f"  > SUCCESS: Created target {target_name}")
            else:
                print(f"  > FAILED: {resp}")
        else:
            print("  > [Dry Run] Would create target.")

if __name__ == "__main__":
    main()
