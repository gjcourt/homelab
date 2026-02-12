import os
import requests
import json
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SYNOLOGY_IP = "192.168.5.8"
SYNOLOGY_PORT = "5001"
SYNOLOGY_USER = os.getenv("SYNOLOGY_USER")
SYNOLOGY_PASS = os.getenv("SYNOLOGY_PASSWORD")

if not SYNOLOGY_USER or not SYNOLOGY_PASS:
    print("Error: SYNOLOGY_USER and SYNOLOGY_PASSWORD environment variables must be set.")
    exit(1)

BASE_URL = f"https://{SYNOLOGY_IP}:{SYNOLOGY_PORT}/webapi"

# ACTIVE_LUNS list from K8s PVs
ACTIVE_LUNS = {
    "00920cd0-352b-4225-98bc-577024de7c92", "02658673-155f-4dc8-84a8-ce1610d48eba", "0b095b54-a620-429d-abf0-ac81ae4e348d",
    "129a80b1-1524-4bbc-a46f-be6dd51c252b", "1e223871-4f60-4820-828e-8609d308c91e", "1f19340c-e5bd-4b28-9a52-91e9465051ad",
    "1fbd1709-ea22-4e09-98e0-10f9a7beed6f", "31c52e5b-96a1-4321-8fcd-d3c42bd418a3", "38f6633a-5dbd-4eb5-a368-d5dcb06f427b",
    "3c4eec92-d93c-4a62-af62-66dfd112f8f0", "3f950bfc-5088-4d3e-a308-93e82f19fa8e", "4ea98b68-592a-4bab-8acc-76ec709fb15d",
    "52fcae27-deda-4063-8ab6-f5b31c4097d4", "5840203e-fa16-481d-ae90-17828f7fd138", "5a913ac7-59f8-4cb3-b9d4-968a92f1acc0",
    "6afd6fe5-07a3-4b87-b6c7-b51bd469b0e5", "6bddeca1-001a-46d0-9131-37779f962a37", "70be9c13-c6c4-428b-8e5a-b23ed5f0399e",
    "70fadbcb-6323-43e2-80cf-dad47c780d4d", "74e74a2b-2e80-4901-9950-622ed17013ed", "7970fe37-6feb-4ae3-934d-1c961c11ac34",
    "8c2fabaf-0ab6-48b6-8044-b14ab5e11588", "8dd28529-92c7-486c-8dcd-732e1a89f8d1", "92c5e4c6-4f93-4c94-857d-8bf07a94544d",
    "93eb3dda-7182-4269-8daf-e84cd5f09a12", "98bcce03-8c3b-4814-ae5b-1e3e501cc1c9", "992745ab-ff81-44a3-b9f5-173c4af75957",
    "9961b9cc-0210-4c05-a917-48f6508a3c59", "9d6d875b-6f52-41b8-8a91-fca35d483c7e", "9fe8f11b-4a7a-438a-98fe-7662b05d3c8c",
    "9ffea139-a03c-4c04-9c39-6d5527f42c85", "a88cd630-c0ae-4325-8f24-cec0d2e7b350", "ac49a69b-7049-4af0-919d-7b57d95ea03f",
    "b7b8fb30-c55d-4765-a2f2-41865d32c0a1", "b88e960c-e69f-41d3-aa10-d3f94ad531f0", "b8a3fa96-752d-4dfa-b3f5-5e8511b704ab",
    "c0ca5457-7daa-45f1-949b-8215cea96795", "c1692176-3282-4af9-81f9-442b8d21d88a", "c236aeeb-1e26-4808-b24b-924ec645d1c6",
    "c4c7762f-bbd7-4383-b80c-6bca5cc8b037", "c5b005a5-af12-4e32-a288-39cc7c7dcf20", "c756af90-4e7e-41c2-8447-c2157d6e2616",
    "cc1455b3-ab75-4e64-a409-f81721e530a6", "db8dcab5-5051-4d6a-bd26-9ff6875f4e67", "dedb8361-12c8-40a6-9aa0-e74c679187b6",
    "e3030294-96d1-4732-b51f-2b48b02f0e48", "f4951635-f1c4-4756-93af-32e502c908f3", "ff7aff42-685d-4f3f-ac40-9e77f3f2d6f1"
}

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
    # Working param set from debug
    params = {
        "api": "SYNO.Core.ISCSI.LUN",
        "version": "1",
        "method": "list"
    }

    try:
        r = session.post(url, data=params, verify=False)
        data = r.json()

        if data.get("success"):
            return data.get("data", {}).get("luns", [])
        else:
            print(f"Failed to list LUNs: {data}")
            return []
    except Exception as e:
        print(f"Error listing LUNs: {e}")
        return []

def main():
    s = requests.Session()
    success, sid = login(s)
    if not success:
        return

    luns = list_luns(s)
    print(f"Found {len(luns)} LUNs on Synology.")

    to_delete = []

    print("\n--- LUN Analysis ---")
    print(f"{'Status':<10} {'Name (PVC)':<50} {'UUID':<40} {'Size (GB)'}")
    print("-" * 115)

    for lun in luns:
        uuid = lun.get('uuid')
        name = lun.get('name')
        desc = lun.get('description', '')
        size_gb = float(lun.get('size', 0)) / (1024*1024*1024)

        display_name = f"{name} ({desc})" if desc else name
        display_name = display_name[:48] # Truncate

        if uuid in ACTIVE_LUNS:
            # print(f"{'KEEP':<10} {display_name:<50} {uuid:<40} {size_gb:.1f}")
            pass
        else:
            print(f"{'DELETE':<10} {display_name:<50} {uuid:<40} {size_gb:.1f}")
            to_delete.append(lun)

    with open('luns_to_delete.json', 'w') as f:
        json.dump(to_delete, f, indent=2)

    print(f"\n{len(to_delete)} LUNs identified for deletion. Saved to luns_to_delete.json")

if __name__ == "__main__":
    main()
