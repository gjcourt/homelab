import os
import requests
import json
import urllib3
import time
import subprocess
import sys

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SYNOLOGY_IP = "192.168.5.8"
SYNOLOGY_PORT = "5001"
SYNOLOGY_USER = os.getenv("SYNOLOGY_USER")
SYNOLOGY_PASS = os.getenv("SYNOLOGY_PASSWORD")

if not SYNOLOGY_USER or not SYNOLOGY_PASS:
    print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD environment variables must be set.")
    exit(1)

BASE_URL = f"https://{SYNOLOGY_IP}:{SYNOLOGY_PORT}/webapi"

def get_k8s_active_pvs():
    """Fetches list of active PV volume handles (UUIDs) from Kubernetes."""
    print("Fetching active PVs from Kubernetes...")
    try:
        # Get all PVs in JSON format
        result = subprocess.run(
            ["kubectl", "get", "pv", "-o", "json"],
            capture_output=True,
            text=True,
            check=True
        )
        pvs = json.loads(result.stdout)
        active_uuids = set()

        for item in pvs.get("items", []):
            # CSI volumeHandle usually formats as "uuid" or similar
            spec = item.get("spec", {})
            csi = spec.get("csi", {})
            driver = csi.get("driver")
            handle = csi.get("volumeHandle")

            # Filter for synology driver if needed, but capturing all usually safer to avoid collisions
            if driver == "csi.san.synology.com" and handle:
                active_uuids.add(handle)

        print(f"Found {len(active_uuids)} active Synology CSI PVs in cluster.")
        return active_uuids

    except subprocess.CalledProcessError as e:
        print("Error: failed to run kubectl.")
        print(e.stderr)
        return None
    except Exception as e:
        print(f"Error parsing K8s output: {e}")
        return None

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
    try:
        r = session.get(auth_url, params=params, verify=False, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            print("Login successful.")
            return True, data.get("data", {}).get("sid")
        else:
            print(f"Login failed: {data}")
            return False, None
    except Exception as e:
        print(f"Login error: {e}")
        return False, None

def list_luns(session):
    url = f"{BASE_URL}/entry.cgi"
    params = {"api": "SYNO.Core.ISCSI.LUN", "version": "1", "method": "list"}
    r = session.post(url, data=params, verify=False)
    data = r.json()
    return data.get("data", {}).get("luns", []) if data.get("success") else []

def list_targets(session):
    url = f"{BASE_URL}/entry.cgi"
    params = {"api": "SYNO.Core.ISCSI.Target", "version": "1", "method": "list"}
    r = session.post(url, data=params, verify=False)
    data = r.json()
    return data.get("data", {}).get("targets", []) if data.get("success") else []

def delete_target(session, target_id, name):
    print(f"  > Deleting Target: {name} (ID: {target_id})... ", end="")
    url = f"{BASE_URL}/entry.cgi"
    params = {"api": "SYNO.Core.ISCSI.Target", "version": "1", "method": "delete", "target_id": target_id}
    r = session.post(url, data=params, verify=False)
    data = r.json()
    if data.get("success"):
        print("OK.")
        return True
    else:
        err_code = data.get('error', {}).get('code')
        if err_code == 18990710:
            print(f"FAILED (Target Busy/Connected). Code: {err_code}")
        else:
            print(f"FAILED. {data}")
        return False

def delete_lun(session, lun_id, uuid, name, active_uuids):
    if uuid in active_uuids:
        print(f"CRITICAL: ATTEMPTED TO DELETE ACTIVE LUN {uuid}. SKIPPING.")
        return False
    print(f"  > Deleting LUN: {name} (ID: {lun_id})... ", end="")
    url = f"{BASE_URL}/entry.cgi"
    params = {"api": "SYNO.Core.ISCSI.LUN", "version": "1", "method": "delete", "lun_id": lun_id}
    r = session.post(url, data=params, verify=False)
    data = r.json()
    if data.get("success"):
        print("OK.")
        return True
    else:
        print(f"FAILED. {data}")
        return False

def main():
    # 1. Get Active PVs
    active_uuids = get_k8s_active_pvs()
    if active_uuids is None:
        print("Could not fetch active PVs from K8s. Aborting for safety.")
        sys.exit(1)

    # 2. Login to Synology
    session = requests.Session()
    success, sid = login(session)
    if not success:
        sys.exit(1)

    print("Fetching LUNs and Targets from Synology...")
    luns = list_luns(session)
    targets = list_targets(session)

    # 3. Identify Orphans
    orphan_luns = []
    active_lun_names = set()

    for lun in luns:
        uuid = lun.get("uuid")
        name = lun.get("name")
        if uuid not in active_uuids:
            orphan_luns.append(lun)
        else:
            active_lun_names.add(name)

    print(f"\nSummary:")
    print(f"  Total LUNs found: {len(luns)}")
    print(f"  Active K8s LUNs: {len(active_uuids)}")
    print(f"  Orphan LUNs to delete: {len(orphan_luns)}")

    # 4. Identify Orphan Targets
    orphan_targets = []

    # Heuristic: If Target Name matches an Orphan LUN Name, mark for deletion.
    # Note: Synology CSI typically names Target same as LUN (e.g. k8s-csi-...)
    orphan_lun_names = {l['name'] for l in orphan_luns}

    for t in targets:
        # Check if this target corresponds to an active LUN
        # This assumes Target Name == LUN Name.
        t_name = t.get("name")

        # Safety check: Is this a k8s-csi target?
        if not t_name.startswith("k8s-csi"):
            continue

        # If this target name matches an ACTIVE lun name, keep it.
        if t_name in active_lun_names:
            continue

        # If it matches an ORPHAN lun name, delete it.
        if t_name in orphan_lun_names:
            orphan_targets.append(t)

        # If Target exists but LUN is already gone
        elif t_name not in active_lun_names:
             orphan_targets.append(t)

    print(f"  Orphan Targets to delete: {len(orphan_targets)}")

    if len(orphan_luns) == 0 and len(orphan_targets) == 0:
        print("Nothing to do.")
        return

    print("\nWARNING: This will permanently delete the orphaned LUNs and Targets listed above.")
    confirm = input("Type 'yes' to proceed: ")
    if confirm != "yes":
        print("Aborted.")
        return

    # 5. Delete Targets First
    print("\n[Phase 1] Deleting Orphan Targets...")
    targets_deleted = 0
    targets_failed = 0

    for t in orphan_targets:
        if delete_target(session, t.get("target_id"), t.get("name")):
            targets_deleted += 1
        else:
            targets_failed += 1

    # 6. Delete LUNs
    print("\n[Phase 2] Deleting Orphan LUNs...")
    luns_deleted = 0
    luns_failed = 0
    for l in orphan_luns:
        if delete_lun(session, l.get("lun_id"), l.get("uuid"), l.get("name"), active_uuids):
            luns_deleted += 1
        else:
            luns_failed += 1

    print("\nFinal Report:")
    print(f"Targets: {targets_deleted} Deleted, {targets_failed} Failed")
    print(f"LUNs:    {luns_deleted} Deleted, {luns_failed} Failed")

    if targets_failed > 0:
        print("\nNOTE: Some targets failed to delete (likely Error 18990710).")
        print("This means the Synology believes there are active iSCSI sessions connected to them.")
        print("To fix this:")
        print("1. Reboot the Synology NAS (or restart iSCSI service).")
        print("2. Run this script again.")

if __name__ == "__main__":
    main()
